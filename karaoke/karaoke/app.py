"""HTTP + WebSocket app. Safe to run several replicas: all state is in Postgres
(karaoke.db), files on a shared volume, and replicas tell each other about
changes with LISTEN/NOTIFY. Each replica only keeps its own open sockets.

Every replica serves pages and phone actions. One of them, the leader
(karaoke.leader), also runs the background work: downloads, photo rotation,
cache eviction and pruning."""

import asyncio
import contextlib
import hmac
import io
import json
import logging
import os
import shutil
import socket
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import asyncpg
import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from karaoke.cancelled import cancel_reason
from karaoke.config import Settings, settings
from karaoke.db import CHANNEL, Store
from karaoke.leader import Leadership
from karaoke.library import Library, valid_video_id
from karaoke.photos import MAX_PENDING_PER_ROOM, MAX_PENDING_PER_UPLOADER, Photo
from karaoke.rooms import ROOM_TTL_SECONDS, ItemStatus, QueueItem, Room
from karaoke.search import (
    SearchResult,
    is_strong,
    lookup_video,
    normalize,
    search_channels,
    search_youtube,
    video_id_from_url,
)

logger = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
MAX_CONCURRENT_DOWNLOADS = 2
PROGRESS_INTERVAL = 1.0
PHOTO_SECONDS = 10
PHOTO_GAP_SECONDS = 1.5
PHOTO_FILE_GRACE = 5  # screens finish fading a photo out before its file goes
TICK_SECONDS = 0.5
DOWNLOAD_SCAN = 5  # seconds between scans for pending downloads, if not woken
REPAIR_EVERY = 15  # seconds between safety-net checks
PRUNE_EVERY = 600
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_QUEUE = 200
SEND_TIMEOUT = 5  # a phone that stops reading is dropped, not waited for
MAX_CONCURRENT_SEARCHES = 4  # per replica; more requests wait their turn
SEARCH_CACHE_SECONDS = 600
SEARCH_CACHE_SIZE = 500
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
    b"RIFF": "image/webp",  # checked further for "WEBP" at offset 8
}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def image_type(data: bytes) -> str | None:
    for magic, mime in IMAGE_SIGNATURES.items():
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                return None
            return mime
    return None


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
    """This replica's sockets, plus the work any replica may pick up."""

    def __init__(self, cfg: Settings, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.worker = os.environ.get("HOSTNAME") or uuid.uuid4().hex[:8]
        self.library = Library(cfg.songs_dir, cfg.max_height)
        self.max_cache_bytes = int(cfg.cache_max_gb * 1024**3)
        self.photos_dir = cfg.photos_dir
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        # Uploads are written here first, then moved in once complete.
        self.upload_dir = cfg.data_dir / "tmp"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self.displays: dict[str, set[WebSocket]] = defaultdict(set)
        self.downloading: set[str] = set()  # by this replica, when leader
        self.download_videos = True  # tests turn it off: no YouTube
        self._wake_downloads = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._leader_tasks: list[asyncio.Task[None]] = []
        self.leadership = Leadership(cfg.dsn(), self._lead, self._follow)
        self._listener: asyncpg.Connection | None = None
        # One push loop per room: pushes stay in order and bursts coalesce.
        self._dirty: set[str] = set()
        self._pushers: dict[str, asyncio.Task[None]] = {}
        self._search_slots = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
        self._search_cache: dict[tuple[str, str], tuple[float, list[SearchResult]]] = {}

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        await self._listen()
        await self.leadership.step()  # the first replica up leads right away
        self._tasks = [
            asyncio.create_task(self._every(5, self._keep_listening)),
            asyncio.create_task(self.leadership.run()),
        ]

    async def stop(self) -> None:
        for task in [*self._tasks, *self._pushers.values()]:
            task.cancel()
        # Hands leadership over at once: the next leader restarts unfinished
        # downloads (the files of this one are never half-written in songs/).
        await self.leadership.close()
        if self._listener is not None:
            await self._listener.close()

    async def _lead(self) -> None:
        await self.store.requeue_interrupted_downloads()
        self._leader_tasks = [
            asyncio.create_task(self._every(TICK_SECONDS, self.rotate_photos)),
            asyncio.create_task(self._download_loop()),
            asyncio.create_task(self._every(REPAIR_EVERY, self.repair)),
            asyncio.create_task(self._every(PRUNE_EVERY, self.prune)),
        ]

    async def _follow(self) -> None:
        # Downloads stop here; the new leader starts them over. A yt-dlp thread
        # already running finishes in its own temp folder, harmlessly.
        for task in list(self._leader_tasks):
            task.cancel()
        self._leader_tasks = []
        self.downloading.clear()

    def _spawn(self, coro: Awaitable[None]) -> None:
        """Fire and forget, keeping a reference so the task is not collected."""
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        task.add_done_callback(self._tasks.remove)

    async def _every(self, seconds: float, job: Callable[[], Awaitable[None]]) -> None:
        while True:
            try:
                await job()
            except Exception:
                logger.exception(
                    "Background job %s failed", getattr(job, "__name__", job)
                )
            await asyncio.sleep(seconds)

    # --- cross-replica notifications ---------------------------------------

    async def _listen(self) -> None:
        self._listener = await asyncpg.connect(self.cfg.dsn())

        def on_message(_conn: object, _pid: int, _channel: str, payload: str) -> None:
            self._spawn(self._dispatch(json.loads(payload)))

        await self._listener.add_listener(CHANNEL, on_message)

    async def _keep_listening(self) -> None:
        if self._listener is not None and not self._listener.is_closed():
            try:  # a half-open connection still looks open: ask it
                await asyncio.wait_for(self._listener.fetchval("SELECT 1"), 5)
                return
            except Exception:
                with contextlib.suppress(Exception):
                    await self._listener.close()
        logger.warning("Lost the LISTEN connection, reconnecting")
        await self._listen()
        for code in list(self.sockets):  # catch up on anything missed
            await self.push_state(code)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        if msg.get("downloads"):
            self._wake_downloads.set()
            return
        code = msg.get("room", "")
        if "command" in msg:
            displays = self.displays.get(code)
            if displays:
                await self._send(displays, {"type": "command", **msg["command"]})
        elif self.sockets.get(code):
            await self.push_state(code)

    async def push_state(self, code: str) -> None:
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
                if not self.sockets.get(code):
                    return
                snapshot = await self.store.snapshot(code)
                if snapshot is None:
                    for ws in list(self.sockets.pop(code, ())):
                        with contextlib.suppress(Exception):
                            await ws.close(code=4404)
                    self.displays.pop(code, None)
                    return
                await self._send(
                    self.sockets.get(code, set()), {"type": "state", "room": snapshot}
                )
        except Exception:
            logger.exception("State push failed for %s", code)
        finally:
            self._pushers.pop(code, None)

    @staticmethod
    async def _send(sockets: set[WebSocket], message: dict[str, Any]) -> None:
        """Send to every socket at once; one slow phone never holds up the
        others, and one that does not take the message in time is dropped
        (its page reconnects and gets the full state)."""

        async def one(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_json(message), SEND_TIMEOUT)
            except Exception:
                sockets.discard(ws)
                with contextlib.suppress(Exception):
                    await ws.close()

        await asyncio.gather(*(one(ws) for ws in list(sockets)))

    # --- downloads ----------------------------------------------------------

    async def ensure_download(self, video_id: str) -> str:
        """Status of the video, reset to pending if its file went missing."""
        status = await self.store.video_status(video_id)
        if status == "ready" and not self.library.exists(video_id):
            await self.store.reset_video(video_id)  # evicted or lost
            status = "pending"
        return status

    async def _download_loop(self) -> None:
        """Leader only: start pending downloads, a few at a time."""
        while True:
            self._wake_downloads.clear()
            try:
                if self.download_videos:
                    for video_id in await self.store.pending_downloads():
                        if len(self.downloading) >= MAX_CONCURRENT_DOWNLOADS:
                            break
                        if video_id not in self.downloading:
                            self.downloading.add(video_id)
                            self._leader_tasks.append(
                                asyncio.create_task(self._download(video_id))
                            )
            except Exception:
                logger.exception("Download scan failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake_downloads.wait(), DOWNLOAD_SCAN)

    async def _download(self, video_id: str) -> None:
        try:
            if not await self.store.start_download(video_id):
                return
            await self._notify_video(video_id)
            ok, title, size = await self._run_download(video_id)
            await self.store.finish_download(video_id, ok, title, size)
            if ok:
                for evicted in await self.store.evict(self.max_cache_bytes):
                    self.library.delete(evicted)
                for code in await self.store.rooms_with_video(video_id):
                    async with self.store.room_tx(code) as tx:
                        if tx and tx.room.current is None:
                            tx.room.advance()
            await self._notify_video(video_id)
        except Exception:
            logger.exception("Download of %s failed", video_id)
        finally:
            self.downloading.discard(video_id)
            self._wake_downloads.set()  # a slot is free
            task = asyncio.current_task()
            if task in self._leader_tasks:
                self._leader_tasks.remove(task)  # type: ignore[arg-type]

    async def _run_download(self, video_id: str) -> tuple[bool, str, int]:
        latest: dict[str, float] = {}

        def on_progress(p: float) -> None:
            latest["p"] = p

        async def report() -> None:
            while True:
                await asyncio.sleep(PROGRESS_INTERVAL)
                if "p" not in latest:
                    continue
                try:
                    await self.store.set_progress(video_id, latest.pop("p"))
                    await self._notify_video(video_id)
                except Exception:  # only the progress bar suffers
                    logger.warning("Progress update failed for %s", video_id)

        reporter = asyncio.create_task(report())
        try:
            title, size = await asyncio.to_thread(
                self.library.download, video_id, self.worker, on_progress
            )
            return True, title, size
        except Exception:
            logger.exception("Download failed for %s", video_id)
            return False, "", 0
        finally:
            reporter.cancel()

    async def repair(self) -> None:
        """Leader safety net: queued songs whose video row or file is gone get
        downloaded again; rooms idle next to a ready song start it."""
        for video_id in await self.store.restore_queued_videos():
            if not self.library.exists(video_id):
                await self.store.reset_video(video_id)
        self._wake_downloads.set()
        for code in await self.store.idle_rooms_with_ready_song():
            async with self.store.room_tx(code) as tx:
                if tx is None:
                    continue
                if tx.room.current is not None:
                    tx.save = False
                    continue
                tx.room.advance()

    async def _notify_video(self, video_id: str) -> None:
        for code in await self.store.rooms_with_video(video_id):
            await self.store.notify_room(code)

    # --- photos -------------------------------------------------------------

    def photo_path(self, code: str, photo_id: str) -> Path:
        return self.photos_dir / code / photo_id

    def forget_photo(self, code: str, photo_id: str, delay: float = 0) -> None:
        path = self.photo_path(code, photo_id)
        if delay:
            asyncio.get_running_loop().call_later(
                delay, lambda: path.unlink(missing_ok=True)
            )
        else:
            path.unlink(missing_ok=True)

    def _hide_photo(self, room: Room) -> None:
        if room.photo is not None:
            self.forget_photo(room.code, room.photo.id, delay=PHOTO_FILE_GRACE)
        room.photo = None
        room.photo_until = time.time() + PHOTO_GAP_SECONDS

    async def rotate_photos(self) -> None:
        """Leader only: advance the photo slot of rooms whose timer ran out."""
        for code in await self.store.rooms_due_for_photo():
            async with self.store.room_tx(code) as tx:
                if tx is None:
                    continue
                room, now = tx.room, time.time()
                if room.photo_until and room.photo_until > now:
                    tx.save = False  # changed meanwhile (photo skipped)
                elif room.photo is not None:
                    self._hide_photo(room)
                elif (photo := room.photos.pop_next()) is not None:
                    room.photo = photo
                    room.photo_until = now + PHOTO_SECONDS
                else:
                    room.photo_until = None

    async def prune(self) -> None:
        for code in await self.store.prune_rooms(ROOM_TTL_SECONDS):
            logger.info("Room %s expired", code)
        live = await self.store.room_codes()
        for folder in self.photos_dir.iterdir():
            if folder.name not in live:
                shutil.rmtree(folder, ignore_errors=True)
        self.library.clean_tmp()
        limit = time.time() - 3600
        for leftover in self.upload_dir.iterdir():  # interrupted uploads
            if leftover.stat().st_mtime < limit:
                leftover.unlink(missing_ok=True)

    # --- search -------------------------------------------------------------

    async def search(self, q: str, everywhere: bool) -> list[SearchResult]:
        """yt-dlp searches, a few at a time per replica and cached for a while:
        hammering YouTube from the home IP could get downloads blocked."""
        link = video_id_from_url(q)
        key = ("link", link) if link else ("all" if everywhere else "cat", normalize(q))
        now = time.monotonic()
        hit = self._search_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        async with self._search_slots:
            if link:
                results = await asyncio.to_thread(lookup_video, link)
            elif everywhere:
                results = await asyncio.to_thread(search_youtube, q)
            else:
                results = await asyncio.to_thread(search_channels, q, self.cfg.channels)
        if results:  # don't remember a failure
            if len(self._search_cache) >= SEARCH_CACHE_SIZE:
                self._search_cache = {
                    k: v for k, v in self._search_cache.items() if v[0] > now
                }
                if len(self._search_cache) >= SEARCH_CACHE_SIZE:
                    self._search_cache.clear()
            self._search_cache[key] = (now + SEARCH_CACHE_SECONDS, results)
        return results

    # --- guest and screen actions ------------------------------------------

    async def handle(self, code: str, msg: dict[str, Any], is_display: bool) -> None:
        kind = msg.get("type")
        video_status = None
        if kind == "add":
            if not valid_video_id(msg.get("video_id")):
                return
            video_status = await self.ensure_download(msg["video_id"])
        elif kind == "retry":
            snapshot = await self.store.snapshot(code) or {}
            failed = [
                i["video_id"]
                for i in snapshot.get("queue", [])
                if i["id"] == msg.get("item_id") and i["status"] == "error"
            ]
            for video_id in failed:
                await self.store.reset_video(video_id)
                await self.store.notify_downloads()
                await self._notify_video(video_id)
            return
        elif kind == "position":
            t = msg.get("t")
            if is_display and isinstance(t, int | float):
                await self.store.save_position(code, str(msg.get("item_id")), t)
            return

        async with self.store.room_tx(code) as tx:
            if tx is None:
                return
            room = tx.room
            if kind == "add":
                if len(room.queue) >= MAX_QUEUE:
                    tx.save = False
                    return
                video_id = msg["video_id"]
                item = QueueItem(
                    video_id=video_id,
                    title=str(msg.get("title", ""))[:200],
                    artist=str(msg.get("artist", ""))[:200],
                    singer=str(msg.get("singer", "")).strip()[:40] or "Anonyme",
                    # Built here, not taken from the phone: every screen loads it.
                    thumbnail=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                    status=cast(ItemStatus, video_status or "pending"),
                )
                item.cancelled = cancel_reason(item.artist, item.title)
                room.add(item)
                if item.status == "ready" and room.current is None:
                    room.advance()
            elif kind == "remove":
                room.remove(str(msg.get("item_id")))
            elif kind == "move":
                room.move(str(msg.get("item_id")), int(msg.get("index", 0)))
            elif kind == "skip":
                room.advance()
            elif kind in ("play", "pause", "restart"):
                if room.current is None:
                    tx.save = False
                    return
                if kind == "pause":
                    room.report_position(room.played())
                elif kind == "restart":
                    room.report_position(0)
                elif room.state != "playing":
                    room.report_position(room.position)
                room.state = "paused" if kind == "pause" else "playing"
                tx.command(kind)
            elif kind == "volume":
                room.volume = max(0.0, min(1.0, float(msg.get("value", room.volume))))
                tx.command("volume", value=room.volume)
            elif kind == "photo_skip":
                if room.photo is None:
                    tx.save = False
                    return
                self._hide_photo(room)
            elif kind == "photo_remove":
                if (
                    room.photos.remove(
                        str(msg.get("photo_id")), str(msg.get("uploader", ""))
                    )
                    is None
                ):
                    tx.save = False
                    return
            elif kind == "ended" and is_display:
                if not room.finish(str(msg.get("item_id"))):
                    tx.save = False
            elif kind == "player" and is_display:
                state = msg.get("state")
                if room.current and state in ("playing", "paused"):
                    room.state = state
                    if isinstance(msg.get("t"), int | float):
                        room.report_position(float(msg["t"]))
                else:
                    tx.save = False
            else:
                tx.save = False
        if tx is not None:
            for photo_id in tx.removed_photos:
                self.forget_photo(code, photo_id)
        if video_status == "pending":
            # Only now that the song is in the queue: the leader downloads
            # queued videos.
            await self.store.notify_downloads()


def create_app(cfg: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        store = await Store.connect(cfg.dsn())
        hub = Hub(cfg, store)
        await hub.start()
        app.state.hub = hub
        try:
            yield
        finally:
            await hub.stop()
            await store.close()

    app = FastAPI(title="Karaoke", docs_url=None, redoc_url=None, lifespan=lifespan)

    def hub_of(request: Request | WebSocket) -> Hub:
        return request.app.state.hub

    def page(name: str) -> FileResponse:
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})

    def join_url(request: Request, code: str) -> str:
        if cfg.public_url:
            return f"{cfg.public_url}/r/{code}"
        url = request.base_url
        if url.hostname in LOOPBACK_HOSTS:
            # Screen opened on localhost: phones need the machine's LAN address.
            url = url.replace(hostname=lan_ip())
        return f"{str(url).rstrip('/')}/r/{code}"

    async def room_or_404(request: Request, code: str) -> str:
        if not await hub_of(request).store.room_exists(code):
            raise HTTPException(404, "Salle introuvable")
        return code.upper()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(request: Request) -> dict[str, str]:
        try:
            await hub_of(request).store.ping()
        except Exception as e:
            raise HTTPException(503, "database unreachable") from e
        return {"status": "ok"}

    @app.get("/")
    async def index() -> FileResponse:
        return page("index.html")

    @app.get("/display/{code}")
    async def display(code: str, request: Request) -> FileResponse:
        await room_or_404(request, code)
        return page("display.html")

    @app.get("/r/{code}")
    async def guest(code: str, request: Request) -> FileResponse:
        await room_or_404(request, code)
        return page("guest.html")

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        return {"password_required": bool(cfg.host_password)}

    @app.post("/api/rooms")
    async def create_room(body: CreateRoom, request: Request) -> dict[str, str]:
        if cfg.host_password and not hmac.compare_digest(
            body.password.encode(), cfg.host_password.encode()
        ):
            raise HTTPException(403, "Mauvais mot de passe")
        code = await hub_of(request).store.create_room()
        logger.info("Created room %s", code)
        return {"code": code}

    @app.get("/api/rooms/{code}")
    async def get_room(code: str, request: Request) -> dict[str, Any]:
        snapshot = await hub_of(request).store.snapshot(code)
        if snapshot is None:
            raise HTTPException(404, "Salle introuvable")
        return {**snapshot, "join_url": join_url(request, snapshot["code"])}

    @app.get("/api/rooms/{code}/qr.svg")
    async def qr(code: str, request: Request) -> Response:
        code = await room_or_404(request, code)
        out = io.BytesIO()
        # Full SVG document: svg_inline() omits xmlns, which <img> renders blank.
        segno.make(join_url(request, code), error="m").save(
            out, kind="svg", scale=10, border=2, dark="#120a24", light="#ffffff"
        )
        return Response(out.getvalue(), media_type="image/svg+xml")

    @app.get("/api/search")
    async def search(
        q: str, room: str, request: Request, everywhere: bool = False
    ) -> dict[str, Any]:
        await room_or_404(request, room)  # only guests of a live room search
        q = q.strip()[:120]
        if not q:
            return {"results": [], "strong": False}
        link = video_id_from_url(q)
        results = await hub_of(request).search(q, everywhere)
        cached = await hub_of(request).store.cached([r.video_id for r in results])
        return {
            "results": [
                {
                    **r.to_dict(),
                    "cached": r.video_id in cached,
                    "cancelled": cancel_reason(r.artist, r.raw_title),
                }
                for r in results
            ],
            # False: the catalogue has nothing matching every word, so the
            # page offers YouTube up front.
            "strong": bool(link) or any(is_strong(q, r) for r in results),
        }

    @app.post("/api/rooms/{code}/photos")
    async def upload_photo(
        code: str, uploader: str, request: Request
    ) -> dict[str, str]:
        hub = hub_of(request)
        code = await room_or_404(request, code)
        uploader = uploader.strip()[:40] or "Anonyme"
        if int(request.headers.get("content-length") or 0) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "Photo trop lourde")
        photo = Photo(uploader=uploader)
        # Streamed to a temporary file with a running count: never the whole
        # body in memory, and cut off as soon as it is too big.
        tmp = hub.upload_dir / photo.id
        size, head = 0, b""
        try:
            with tmp.open("wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_PHOTO_BYTES:
                        raise HTTPException(413, "Photo trop lourde")
                    if len(head) < 12:
                        head = (head + chunk)[:12]
                        if len(head) == 12 and image_type(head) is None:
                            raise HTTPException(
                                415, "Format non supporté (JPEG, PNG ou WebP)"
                            )
                    await asyncio.to_thread(out.write, chunk)
            if image_type(head) is None:
                raise HTTPException(415, "Format non supporté (JPEG, PNG ou WebP)")
            if not await hub.store.add_photo(code, photo.id, uploader):
                raise HTTPException(
                    429,
                    f"Maximum {MAX_PENDING_PER_UPLOADER} photos en attente par "
                    f"personne, {MAX_PENDING_PER_ROOM} par salle",
                )
            path = hub.photo_path(code, photo.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        await hub.store.notify_room(code)
        return {"id": photo.id}

    @app.get("/photos/{code}/{photo_id}")
    async def photo_file(code: str, photo_id: str, request: Request) -> Response:
        if not photo_id.isalnum() or not code.isalnum():
            raise HTTPException(404)
        path = hub_of(request).photo_path(code.upper(), photo_id)
        try:
            with path.open("rb") as f:
                head = f.read(12)
        except OSError as e:
            raise HTTPException(404) from e
        return FileResponse(
            path,
            media_type=image_type(head) or "application/octet-stream",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/media/{video_id}.mp4")
    async def media(video_id: str, request: Request) -> FileResponse:
        library = hub_of(request).library
        if not library.exists(video_id):  # also rejects anything but a YouTube id
            raise HTTPException(404)
        return FileResponse(library.path_for(video_id), media_type="video/mp4")

    @app.websocket("/ws/{code}")
    async def ws(websocket: WebSocket, code: str, role: str = "guest") -> None:
        hub = hub_of(websocket)
        code = code.upper()
        await websocket.accept()
        is_display = role == "display"
        # Registered before reading the state, so no change can slip between
        # the snapshot and the first push.
        hub.sockets[code].add(websocket)
        if is_display:
            hub.displays[code].add(websocket)
        try:
            snapshot = await hub.store.snapshot(code)
            if snapshot is None:
                await websocket.close(code=4404)
                return
            await websocket.send_json({"type": "state", "room": snapshot})
            while True:
                msg = await websocket.receive_json()
                if not isinstance(msg, dict):
                    continue
                try:
                    await hub.handle(code, msg, is_display)
                except (KeyError, ValueError, TypeError):
                    logger.warning("Bad message in %s: %r", code, msg)
        except WebSocketDisconnect:
            pass
        except ValueError:  # not JSON
            with contextlib.suppress(Exception):
                await websocket.close(code=1003)
        finally:
            hub.sockets[code].discard(websocket)
            if code in hub.displays:
                hub.displays[code].discard(websocket)
            if not hub.sockets[code]:
                hub.sockets.pop(code, None)
                hub.displays.pop(code, None)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
