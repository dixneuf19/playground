"""The open sockets: every page keeps one. The hub applies what they send to
the show and pushes the show back to everyone when it changed.

Trust model: a party among friends. Only the control room is behind a
password; a camera is whoever scanned the QR code.
"""

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from coco_lanta.show import TEAMS, Show

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 5  # a phone that stops reading is dropped, not waited for
KICKED = 4403  # close code: the camera page stops and says so
# Captioned reactions for one game: phones only show them while the control
# room has turned that game on.
SIDES = {"☭": "extrême gauche", "🤑": "extrême centre", "🚫": "extrême droite"}
EMOJI = (*SIDES, "🔥", "👏", "😂", "❤️", "🌴")
CARD_MAX = 100
REACTIONS = (*EMOJI, *TEAMS)  # a team name floats up as its icon
COMMENT_MAX = 80
NAME_MAX = 24


def clean(text: object, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


@dataclass
class Bucket:
    """Token bucket: `rate` per second, bursts up to `burst`."""

    rate: float
    burst: float
    tokens: float = 0.0
    at: float = 0.0

    def __post_init__(self) -> None:
        self.tokens = self.burst

    def take(self, now: float) -> bool:
        self.tokens = min(self.burst, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


@dataclass(eq=False)
class Client:
    ws: WebSocket
    role: str  # tv, regie, cam or react
    cam_id: str = ""
    reactions: Bucket = field(default_factory=lambda: Bucket(8, 30))
    comments: Bucket = field(default_factory=lambda: Bucket(0.5, 3))


class Hub:
    def __init__(self) -> None:
        self.show = Show()
        self.clients: set[Client] = set()
        self._sent = ""
        self._ticker: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._ticker = asyncio.create_task(self._tick())

    async def stop(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                self.show.tick(time.time())
                await self.sync()
            except Exception:
                logger.exception("Tick failed")

    # --- sockets ------------------------------------------------------------

    async def join(self, client: Client, name: str = "") -> None:
        self.clients.add(client)
        if client.role == "cam":
            self.show.join(client.cam_id, name, time.time())
        if not await self.sync():  # unchanged: nobody was sent anything
            await self._send([client], self._state())

    async def leave(self, client: Client) -> None:
        self.clients.discard(client)
        # A camera reconnecting on a new socket has already taken its place.
        still_here = any(c.cam_id == client.cam_id for c in self.clients)
        if client.role == "cam" and not still_here:
            self.show.leave(client.cam_id, time.time())

    def _state(self) -> str:
        return json.dumps(
            {"type": "state", "show": self.show.snapshot(), "now": time.time()}
        )

    async def sync(self) -> bool:
        """Push the show to everyone if it changed."""
        snapshot = json.dumps(self.show.snapshot())
        if snapshot == self._sent:
            return False
        self._sent = snapshot
        await self._send(list(self.clients), self._state())
        return True

    async def _send(self, clients: list[Client], text: str) -> None:
        async def one(client: Client) -> None:
            try:
                await asyncio.wait_for(client.ws.send_text(text), SEND_TIMEOUT)
            except Exception:
                self.clients.discard(client)
                with contextlib.suppress(Exception):
                    await client.ws.close()

        await asyncio.gather(*(one(c) for c in clients))

    async def _event(self, roles: tuple[str, ...], **event: Any) -> None:
        targets = [c for c in self.clients if c.role in roles]
        await self._send(targets, json.dumps(event))

    # --- messages -----------------------------------------------------------

    async def handle(self, client: Client, msg: dict[str, Any]) -> None:
        now = time.time()
        kind = msg.get("type")
        if kind == "react":
            emoji = msg.get("emoji")
            allowed = emoji in REACTIONS and (self.show.sides or emoji not in SIDES)
            if allowed and client.reactions.take(now):
                await self._event(("tv",), type="react", emoji=emoji)
        elif kind == "comment":
            text = clean(msg.get("text"), COMMENT_MAX)
            if text and client.comments.take(now):
                name = clean(msg.get("name"), NAME_MAX) or "Anonyme"
                await self._event(("tv", "regie"), type="comment", name=name, text=text)
        elif kind == "speakers" and client.role == "tv":
            ids = msg.get("ids")
            if isinstance(ids, list):
                self.show.speakers([str(i) for i in ids[:10]], now)
        elif kind == "cmd" and client.role == "regie":
            await self._command(msg, now)
        await self.sync()

    async def _command(self, msg: dict[str, Any], now: float) -> None:
        show = self.show
        match msg.get("cmd"):
            case "take":
                show.take(str(msg.get("id")), now)
            case "hear":
                show.hear(str(msg.get("id")), bool(msg.get("on")))
            case "auto":
                show.set_auto(now)
            case "layout":
                show.set_layout(str(msg.get("layout")), now)
            case "score":
                team, delta = msg.get("team"), msg.get("delta")
                if team in TEAMS and delta in (1, -1):
                    show.score(team, delta)
            case "score_reset":
                show.scores = dict.fromkeys(TEAMS, 0)
            case "timer_start":
                seconds = msg.get("seconds")
                if isinstance(seconds, int | float):
                    show.timer_start(float(seconds), now)
            case "timer_toggle":
                show.timer_toggle(now)
            case "timer_reset":
                show.timer_reset()
            case "card":
                show.card = clean(msg.get("text"), CARD_MAX)
            case "audio":
                show.audio = bool(msg.get("on"))
            case "sides":
                show.sides = bool(msg.get("on"))
            case "qr":
                show.qr = bool(msg.get("on"))
            case "stinger":
                text = clean(msg.get("text"), CARD_MAX)
                if text:
                    await self._event(("tv",), type="stinger", text=text)
            case "kick":
                cam_id = str(msg.get("id"))
                show.kick(cam_id, now)
                for c in [c for c in self.clients if c.cam_id == cam_id]:
                    self.clients.discard(c)
                    with contextlib.suppress(Exception):
                        await c.ws.close(code=KICKED)
