import asyncio
import hmac
import io
import logging
import socket
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from karaoke.cancelled import cancel_reason
from karaoke.config import Settings, settings
from karaoke.library import Library
from karaoke.rooms import QueueItem, Room, RoomRegistry
from karaoke.search import search_channels, search_youtube

logger = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
MAX_CONCURRENT_DOWNLOADS = 2
PROGRESS_BROADCAST_INTERVAL = 0.5


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def lan_ip() -> str:
    """IP of the interface holding the default route (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class CreateRoom(BaseModel):
    password: str = ""


class Hub:
    """Room state plus the sockets watching it, and the download pipeline."""

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.rooms = RoomRegistry()
        self.library = Library(
            cfg.songs_dir, cfg.max_height, int(cfg.cache_max_gb * 1024**3)
        )
        self.sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self.displays: dict[str, set[WebSocket]] = defaultdict(set)
        self.downloads: dict[str, asyncio.Task[None]] = {}
        self.download_slots = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        self._last_progress_push: dict[str, float] = {}

    # --- broadcasting -------------------------------------------------------

    async def broadcast(self, room: Room) -> None:
        message = {"type": "state", "room": room.snapshot()}
        for ws in list(self.sockets[room.code]):
            try:
                await ws.send_json(message)
            except Exception:
                self.sockets[room.code].discard(ws)

    async def command(self, room: Room, action: str, **extra: Any) -> None:
        for ws in list(self.displays[room.code]):
            try:
                await ws.send_json({"type": "command", "action": action, **extra})
            except Exception:
                self.displays[room.code].discard(ws)

    def items_for(self, video_id: str) -> list[tuple[Room, QueueItem]]:
        return [
            (room, item)
            for room in self.rooms.all()
            for item in room.queue
            if item.video_id == video_id
        ]

    # --- downloads ----------------------------------------------------------

    def ensure_download(self, video_id: str) -> None:
        if self.library.has(video_id):
            self.library.touch(video_id)
            for _, item in self.items_for(video_id):
                item.status, item.progress = "ready", 1.0
            return
        if video_id not in self.downloads:
            self.downloads[video_id] = asyncio.create_task(self._download(video_id))

    async def _download(self, video_id: str) -> None:
        loop = asyncio.get_running_loop()

        def on_progress(p: float) -> None:
            loop.call_soon_threadsafe(self._on_progress, video_id, p)

        try:
            async with self.download_slots:
                for _, item in self.items_for(video_id):
                    item.status = "downloading"
                await self._broadcast_video(video_id)
                await asyncio.to_thread(self.library.download, video_id, on_progress)
            status = "ready"
        except Exception:
            logger.exception("Download failed for %s", video_id)
            status = "error"
        finally:
            self.downloads.pop(video_id, None)
        for room, item in self.items_for(video_id):
            item.status = status
            item.progress = 1.0 if status == "ready" else 0.0
            if room.current is None and status == "ready":
                room.advance()
        await self._broadcast_video(video_id)

    def _on_progress(self, video_id: str, progress: float) -> None:
        for _, item in self.items_for(video_id):
            item.progress = progress
        now = time.monotonic()
        if (
            now - self._last_progress_push.get(video_id, 0)
            > PROGRESS_BROADCAST_INTERVAL
        ):
            self._last_progress_push[video_id] = now
            asyncio.create_task(self._broadcast_video(video_id))  # noqa: RUF006

    async def _broadcast_video(self, video_id: str) -> None:
        rooms = {room.code: room for room, _ in self.items_for(video_id)}
        for room in self.rooms.all():
            if room.current and room.current.video_id == video_id:
                rooms[room.code] = room
        for room in rooms.values():
            await self.broadcast(room)

    # --- guest and screen actions ------------------------------------------

    async def handle(self, room: Room, msg: dict[str, Any], is_display: bool) -> None:
        room.touch()
        kind = msg.get("type")
        if kind == "add":
            item = QueueItem(
                video_id=str(msg["video_id"]),
                title=str(msg.get("title", ""))[:200],
                artist=str(msg.get("artist", ""))[:200],
                singer=str(msg.get("singer", "")).strip()[:40] or "Anonyme",
                thumbnail=str(msg.get("thumbnail", "")),
            )
            item.cancelled = cancel_reason(item.artist, item.title)
            room.add(item)
            self.ensure_download(item.video_id)
            if item.status == "ready" and room.current is None:
                room.advance()
        elif kind == "remove":
            room.remove(str(msg.get("item_id")))
        elif kind == "move":
            room.move(str(msg.get("item_id")), int(msg.get("index", 0)))
        elif kind == "retry":
            item = room.find(str(msg.get("item_id")))
            if item and item.status == "error":
                item.status = "pending"
                self.ensure_download(item.video_id)
        elif kind == "skip":
            room.advance()
        elif kind in ("play", "pause", "restart"):
            if room.current is None:
                return
            room.state = "paused" if kind == "pause" else "playing"
            await self.command(room, kind)
        elif kind == "volume":
            room.volume = max(0.0, min(1.0, float(msg.get("value", room.volume))))
            await self.command(room, "volume", value=room.volume)
        elif kind == "ended" and is_display:
            if not room.finish(str(msg.get("item_id"))):
                return
        elif kind == "player" and is_display:
            state = msg.get("state")
            if room.current and state in ("playing", "paused"):
                room.state = state
        else:
            return
        await self.broadcast(room)


def create_app(cfg: Settings = settings) -> FastAPI:
    app = FastAPI(title="Karaoke", docs_url=None, redoc_url=None)
    hub = Hub(cfg)
    app.state.hub = hub

    def page(name: str) -> FileResponse:
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})

    def join_url(request: Request, room: Room) -> str:
        if cfg.public_url:
            return f"{cfg.public_url}/r/{room.code}"
        url = request.base_url
        if url.hostname in LOOPBACK_HOSTS:
            # Screen opened on localhost: phones need the machine's LAN address.
            url = url.replace(hostname=lan_ip())
        return f"{str(url).rstrip('/')}/r/{room.code}"

    def room_or_404(code: str) -> Room:
        room = hub.rooms.get(code)
        if room is None:
            raise HTTPException(404, "Salle introuvable")
        return room

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def index() -> FileResponse:
        return page("index.html")

    @app.get("/display/{code}")
    async def display(code: str) -> FileResponse:
        room_or_404(code)
        return page("display.html")

    @app.get("/r/{code}")
    async def guest(code: str) -> FileResponse:
        room_or_404(code)
        return page("guest.html")

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        return {"password_required": bool(cfg.host_password)}

    @app.post("/api/rooms")
    async def create_room(body: CreateRoom) -> dict[str, str]:
        if cfg.host_password and not hmac.compare_digest(
            body.password.encode(), cfg.host_password.encode()
        ):
            raise HTTPException(403, "Mauvais mot de passe")
        room = hub.rooms.create()
        logger.info("Created room %s", room.code)
        return {"code": room.code}

    @app.get("/api/rooms/{code}")
    async def get_room(code: str, request: Request) -> dict[str, Any]:
        room = room_or_404(code)
        return {**room.snapshot(), "join_url": join_url(request, room)}

    @app.get("/api/rooms/{code}/qr.svg")
    async def qr(code: str, request: Request) -> Response:
        room = room_or_404(code)
        out = io.BytesIO()
        # Full SVG document: svg_inline() omits xmlns, which <img> renders blank.
        segno.make(join_url(request, room), error="m").save(
            out, kind="svg", scale=10, border=2, dark="#120a24", light="#ffffff"
        )
        return Response(out.getvalue(), media_type="image/svg+xml")

    @app.get("/api/search")
    async def search(q: str, everywhere: bool = False) -> dict[str, Any]:
        q = q.strip()[:120]
        if not q:
            return {"results": []}
        if everywhere:
            results = await asyncio.to_thread(search_youtube, q)
        else:
            results = await asyncio.to_thread(search_channels, q, cfg.channels)
        return {
            "results": [
                {
                    **r.to_dict(),
                    "cached": hub.library.has(r.video_id),
                    "cancelled": cancel_reason(r.artist, r.raw_title),
                }
                for r in results
            ]
        }

    @app.get("/media/{video_id}.mp4")
    async def media(video_id: str) -> FileResponse:
        if not hub.library.has(video_id):
            raise HTTPException(404)
        return FileResponse(hub.library.path_for(video_id), media_type="video/mp4")

    @app.websocket("/ws/{code}")
    async def ws(websocket: WebSocket, code: str, role: str = "guest") -> None:
        room = hub.rooms.get(code)
        await websocket.accept()
        if room is None:
            await websocket.close(code=4404)
            return
        is_display = role == "display"
        hub.sockets[room.code].add(websocket)
        if is_display:
            hub.displays[room.code].add(websocket)
        await websocket.send_json({"type": "state", "room": room.snapshot()})
        try:
            while True:
                msg = await websocket.receive_json()
                try:
                    await hub.handle(room, msg, is_display)
                except (KeyError, ValueError, TypeError):
                    logger.warning("Bad message in %s: %r", room.code, msg)
        except WebSocketDisconnect:
            pass
        finally:
            hub.sockets[room.code].discard(websocket)
            hub.displays[room.code].discard(websocket)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
