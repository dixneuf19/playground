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
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import WebSocket

from karaoke.db import CHANNEL, Store
from karaoke.favourites import Favourite
from karaoke.loops import every
from karaoke.rooms import Room
from karaoke.views import room_videos

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 5  # a phone that stops reading is dropped, not waited for
ROOM_GONE = 4404  # close code: the page goes back home
TRY_AGAIN = 1013  # close code: the page reconnects and gets the full state
HEARTBEAT = 30  # seconds between presence updates (see db.NAME_RELEASE_SECONDS)

RenderGuest = Callable[[Room, str, set[str], list[Favourite] | None], str]


@dataclass
class Extras:
    """What a phone shows besides the room: which of its songs are hearted,
    and the favourites list when it changed (None: not part of this push)."""

    hearted: set[str]
    favourites: list[Favourite] | None


@dataclass(eq=False)
class Client:
    ws: WebSocket
    screen: bool = False
    # The htmx phone page: pushed HTML fragments for this guest, not JSON.
    html: bool = False
    # The phone's token. Its name in the room is read from the database at
    # each push, so a rename or a takeover shows on the very next one.
    token: str = ""


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
        self._heart: asyncio.Task[None] | None = None
        # One push loop per room: pushes stay in order and bursts coalesce.
        self._dirty: set[str] = set()
        # Rooms whose next push carries the favourites list (it changed).
        self._favourites_dirty: set[str] = set()
        self._pushers: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        await self._listen()
        self._keeper = asyncio.create_task(every(5, self._keep_listening))
        self._heart = asyncio.create_task(every(HEARTBEAT, self.heartbeat))

    async def stop(self) -> None:
        for task in [self._keeper, self._heart, *self._tasks, *self._pushers.values()]:
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
        if client.token:
            await self.store.touch_guests([(code, client.token)], time.time())
        names = await self._names(code, [client])
        extras = await self._extras(room, [client], with_favourites=True)
        await client.ws.send_text(self._payload(room, client, names, extras, {}))
        return True

    def leave(self, code: str, client: Client) -> None:
        clients = self.clients.get(code)
        if clients is None:
            return
        clients.discard(client)
        if not clients:
            self.clients.pop(code, None)

    async def heartbeat(self) -> None:
        """Presence, for every replica to see: this replica's phones are still
        here. Each replica refreshes its own sockets; a name nobody refreshes
        (phone gone, pod dead) is released after a while (db.claim_name)."""
        phones = {
            (code, c.token)
            for code, clients in list(self.clients.items())
            for c in list(clients)
            if c.token
        }
        await self.store.touch_guests(sorted(phones), time.time())

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
            self.push_state(code, favourites=True)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if msg.get("downloads"):
            self.on_downloads()
            return
        # Shared by every room. Cancellations change flags in the queue and
        # in the favourites list too.
        if msg.get("favourites") or msg.get("cancellations"):
            for room_code in list(self.clients):
                self.push_state(room_code, favourites=True)
            return
        code = msg.get("room", "")
        if "command" in msg:
            screens = [c for c in self.clients.get(code, ()) if c.screen]
            text = json.dumps({"type": "command", **msg["command"]})
            await self._send([(c, code, text) for c in screens])
        elif self.clients.get(code):
            self.push_state(code)

    # --- state pushes -------------------------------------------------------

    def push_state(self, code: str, favourites: bool = False) -> None:
        """Ask for a fresh state push. Changes arriving while a push is under
        way are folded into one more push, so phones never see an older state
        after a newer one. `favourites`: also send the favourites list, which
        plain room pushes leave out (it changes rarely and weighs a lot)."""
        self._dirty.add(code)
        if favourites:
            self._favourites_dirty.add(code)
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
                with_favourites = code in self._favourites_dirty
                self._favourites_dirty.discard(code)
                clients = list(self.clients.get(code, ()))
                names = await self._names(code, clients)
                extras = await self._extras(room, clients, with_favourites)
                rendered: dict[Any, str] = {}
                await self._send(
                    [
                        (c, code, self._payload(room, c, names, extras, rendered))
                        for c in clients
                    ]
                )
        except Exception:
            logger.exception("State push failed for %s", code)
        finally:
            self._pushers.pop(code, None)

    async def _names(self, code: str, clients: list[Client]) -> dict[str, str]:
        """token -> name in the room, read only when an htmx phone needs it."""
        if not any(c.html and c.token for c in clients):
            return {}
        return await self.store.guest_names(code)

    async def _extras(
        self, room: Room, clients: list[Client], with_favourites: bool
    ) -> Extras:
        if not any(c.html for c in clients):  # screens don't show favourites
            return Extras(set(), None)
        return Extras(
            await self.store.hearted(room_videos(room)),
            await self.store.favourites() if with_favourites else None,
        )

    def _payload(
        self,
        room: Room,
        client: Client,
        names: dict[str, str],
        extras: Extras,
        rendered: dict[Any, str],
    ) -> str:
        """What this client gets for this state, rendered once per kind."""
        name = names.get(client.token, "")
        key = ("html", name) if client.html else "json"
        if key not in rendered:
            rendered[key] = (
                self.render_guest(room, name, extras.hearted, extras.favourites)
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
