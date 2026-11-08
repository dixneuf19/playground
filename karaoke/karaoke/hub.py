"""This replica's open sockets, and the cross-replica notifications that tell
them a room changed.

Every change is committed with a NOTIFY (karaoke.db); every replica LISTENs,
re-reads the room and pushes it to its own sockets. Screens and old phone pages
get the state as JSON; the htmx phone page gets HTML rendered for its guest.
"""

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import WebSocket

from karaoke.db import CHANNEL, Store
from karaoke.loops import every
from karaoke.rooms import Room

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 5  # a phone that stops reading is dropped, not waited for
ROOM_GONE = 4404  # close code: the page goes back home
TRY_AGAIN = 1013  # close code: the page reconnects and gets the full state

RenderGuest = Callable[[Room, str], str]


@dataclass(eq=False)
class Client:
    ws: WebSocket
    screen: bool = False
    # The htmx phone page: pushed HTML fragments for this guest, not JSON.
    html: bool = False
    name: str = ""


class Hub:
    def __init__(
        self,
        dsn: str,
        store: Store,
        render_guest: RenderGuest,
        on_downloads: Callable[[], None],
    ) -> None:
        self.dsn = dsn
        self.store = store
        self.render_guest = render_guest
        self.on_downloads = on_downloads
        self.clients: dict[str, set[Client]] = defaultdict(set)
        self._listener: asyncpg.Connection | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._keeper: asyncio.Task[None] | None = None
        # One push loop per room: pushes stay in order and bursts coalesce.
        self._dirty: set[str] = set()
        self._pushers: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        await self._listen()
        self._keeper = asyncio.create_task(every(5, self._keep_listening))

    async def stop(self) -> None:
        for task in [self._keeper, *self._tasks, *self._pushers.values()]:
            if task is not None:
                task.cancel()
        if self._listener is not None:
            await self._listener.close()

    # --- sockets ------------------------------------------------------------

    async def join(self, code: str, client: Client) -> bool:
        """Register a socket and send it the room. False if the room is gone.

        Registered before reading the state, so no change can slip between
        the first message and the next push."""
        self.clients[code].add(client)
        room = await self.store.load_room(code)
        if room is None:
            return False
        await client.ws.send_text(self._payload(room, client, {}))
        return True

    def leave(self, code: str, client: Client) -> None:
        clients = self.clients.get(code)
        if clients is None:
            return
        clients.discard(client)
        if not clients:
            self.clients.pop(code, None)

    # --- cross-replica notifications ---------------------------------------

    async def _listen(self) -> None:
        self._listener = await asyncpg.connect(self.dsn)

        def on_message(_conn: object, _pid: int, _channel: str, payload: str) -> None:
            task = asyncio.create_task(self._dispatch(json.loads(payload)))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        await self._listener.add_listener(CHANNEL, on_message)

    async def _keep_listening(self) -> None:
        """Reconnect a LISTEN connection that died."""
        if self._listener is not None and not self._listener.is_closed():
            try:  # a half-open connection still looks open: ask it
                await asyncio.wait_for(self._listener.fetchval("SELECT 1"), 5)
                return
            except Exception:
                with contextlib.suppress(Exception):
                    await self._listener.close()
        logger.warning("Lost the LISTEN connection, reconnecting")
        await self._listen()
        for code in list(self.clients):  # catch up on anything missed
            self.push_state(code)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if msg.get("downloads"):
            self.on_downloads()
            return
        code = msg.get("room", "")
        if "command" in msg:
            screens = [c for c in self.clients.get(code, ()) if c.screen]
            text = json.dumps({"type": "command", **msg["command"]})
            await self._send([(c, code, text) for c in screens])
        elif self.clients.get(code):
            self.push_state(code)

    # --- state pushes -------------------------------------------------------

    def push_state(self, code: str) -> None:
        """Ask for a fresh state push. Changes arriving while a push is under
        way are folded into one more push, so phones never see an older state
        after a newer one."""
        self._dirty.add(code)
        if code not in self._pushers:
            self._pushers[code] = asyncio.create_task(self._push_loop(code))

    async def _push_loop(self, code: str) -> None:
        try:
            while code in self._dirty:
                self._dirty.discard(code)
                if not self.clients.get(code):
                    return
                room = await self.store.load_room(code)
                if room is None:
                    for client in list(self.clients.pop(code, ())):
                        with contextlib.suppress(Exception):
                            await client.ws.close(code=ROOM_GONE)
                    return
                rendered: dict[Any, str] = {}
                await self._send(
                    [
                        (c, code, self._payload(room, c, rendered))
                        for c in list(self.clients.get(code, ()))
                    ]
                )
        except Exception:
            logger.exception("State push failed for %s", code)
        finally:
            self._pushers.pop(code, None)

    def _payload(self, room: Room, client: Client, rendered: dict[Any, str]) -> str:
        """What this client gets for this state, rendered once per kind."""
        key = ("html", client.name) if client.html else "json"
        if key not in rendered:
            rendered[key] = (
                self.render_guest(room, client.name)
                if client.html
                else json.dumps({"type": "state", "room": room.snapshot()})
            )
        return rendered[key]

    async def _send(self, messages: list[tuple[Client, str, str]]) -> None:
        """Send to every socket at once; one slow phone never holds up the
        others, and one that does not take its message in time is dropped
        (its page reconnects and gets the full state)."""

        async def one(client: Client, code: str, text: str) -> None:
            try:
                await asyncio.wait_for(client.ws.send_text(text), SEND_TIMEOUT)
            except Exception:
                self.leave(code, client)
                with contextlib.suppress(Exception):
                    await client.ws.close(code=TRY_AGAIN)

        await asyncio.gather(*(one(*m) for m in messages))
