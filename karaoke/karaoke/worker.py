"""Background work, run by the leader replica only (karaoke.leader): downloads,
photo rotation, a repair pass and pruning. Every other replica only serves.

A replica that loses leadership stops all of it; the next leader starts the
unfinished downloads over (their files are never half-written in songs/).
"""

import asyncio
import contextlib
import logging
import os
import time
import uuid

from karaoke.config import Settings
from karaoke.db import Store
from karaoke.leader import Leadership
from karaoke.library import Library
from karaoke.loops import every
from karaoke.photo_files import PhotoFiles
from karaoke.rooms import ROOM_TTL_SECONDS

logger = logging.getLogger(__name__)

MAX_CONCURRENT_DOWNLOADS = 2
PROGRESS_INTERVAL = 1.0
TICK_SECONDS = 0.5
DOWNLOAD_SCAN = 5  # seconds between scans for pending downloads, if not woken
REPAIR_EVERY = 15  # seconds between safety-net checks
PRUNE_EVERY = 600


class Worker:
    def __init__(
        self, cfg: Settings, store: Store, library: Library, photos: PhotoFiles
    ) -> None:
        self.store = store
        self.library = library
        self.photos = photos
        self.name = os.environ.get("HOSTNAME") or uuid.uuid4().hex[:8]
        self.max_cache_bytes = int(cfg.cache_max_gb * 1024**3)
        self.download_videos = True  # tests turn it off: no YouTube
        self.downloading: set[str] = set()  # by this replica, while it leads
        self._wake_downloads = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._election: asyncio.Task[None] | None = None
        self.leadership = Leadership(cfg.dsn(), self._lead, self._follow)

    async def start(self) -> None:
        await self.leadership.step()  # the first replica up leads right away
        self._election = asyncio.create_task(self.leadership.run())

    async def stop(self) -> None:
        if self._election is not None:
            self._election.cancel()
        # Hands leadership over at once: the next leader restarts unfinished
        # downloads.
        await self.leadership.close()

    def wake(self) -> None:
        """A video waits to be downloaded (NOTIFY from any replica)."""
        self._wake_downloads.set()

    async def _lead(self) -> None:
        await self.store.requeue_interrupted_downloads()
        self._tasks = [
            asyncio.create_task(every(TICK_SECONDS, self.rotate_photos)),
            asyncio.create_task(self._download_loop()),
            asyncio.create_task(every(REPAIR_EVERY, self.repair)),
            asyncio.create_task(every(PRUNE_EVERY, self.prune)),
        ]

    async def _follow(self) -> None:
        # A yt-dlp thread already running finishes in its own temp folder,
        # harmlessly.
        for task in list(self._tasks):
            task.cancel()
        self._tasks = []
        self.downloading.clear()

    # --- downloads ----------------------------------------------------------

    async def _download_loop(self) -> None:
        """Start pending downloads, a few at a time."""
        while True:
            self._wake_downloads.clear()
            try:
                if self.download_videos:
                    for video_id in await self.store.pending_downloads():
                        if len(self.downloading) >= MAX_CONCURRENT_DOWNLOADS:
                            break
                        if video_id not in self.downloading:
                            self.downloading.add(video_id)
                            self._tasks.append(
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
                        if tx is not None:
                            tx.save = tx.room.start_if_idle()
            await self._notify_video(video_id)
        except Exception:
            logger.exception("Download of %s failed", video_id)
        finally:
            self.downloading.discard(video_id)
            self._wake_downloads.set()  # a slot is free
            task = asyncio.current_task()
            if task in self._tasks:
                self._tasks.remove(task)  # type: ignore[arg-type]

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
                self.library.download, video_id, self.name, on_progress
            )
            return True, title, size
        except Exception:
            logger.exception("Download failed for %s", video_id)
            return False, "", 0
        finally:
            reporter.cancel()

    async def _notify_video(self, video_id: str) -> None:
        for code in await self.store.rooms_with_video(video_id):
            await self.store.notify_room(code)

    # --- periodic jobs ------------------------------------------------------

    async def repair(self) -> None:
        """Safety net: queued songs whose video row or file is gone get
        downloaded again; rooms idle next to a ready song start it."""
        for video_id in await self.store.restore_queued_videos():
            if not self.library.exists(video_id):
                await self.store.reset_video(video_id)
        self._wake_downloads.set()
        for code in await self.store.idle_rooms_with_ready_song():
            async with self.store.room_tx(code) as tx:
                if tx is not None:
                    tx.save = tx.room.start_if_idle()

    async def rotate_photos(self) -> None:
        """Advance the photo slot of rooms whose timer ran out."""
        now = time.time()
        for code in await self.store.rooms_due_for_photo(now):
            async with self.store.room_tx(code) as tx:
                if tx is not None:
                    tx.save = tx.room.rotate_photo(now)
            self.photos.cleanup(tx)

    async def prune(self) -> None:
        for code in await self.store.prune_rooms(ROOM_TTL_SECONDS):
            logger.info("Room %s expired", code)
        self.photos.prune(await self.store.room_codes())
        self.library.clean_tmp()
