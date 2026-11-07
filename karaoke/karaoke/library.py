"""Video files on the shared volume. Which video is cached, its size and when it
was last played live in Postgres (karaoke.db), shared by every replica."""

import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]

# YouTube ids only: they end up in file paths and URLs.
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
TMP_MAX_AGE = 3600  # leftovers of interrupted downloads


def valid_video_id(video_id: object) -> bool:
    return isinstance(video_id, str) and VIDEO_ID.fullmatch(video_id) is not None


class Library:
    def __init__(self, songs_dir: Path, max_height: int) -> None:
        self.dir = songs_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_height = max_height

    @property
    def tmp_dir(self) -> Path:
        return self.dir / ".tmp"

    def path_for(self, video_id: str) -> Path:
        if not valid_video_id(video_id):
            msg = f"Invalid video id {video_id!r}"
            raise ValueError(msg)
        return self.dir / f"{video_id}.mp4"

    def exists(self, video_id: str) -> bool:
        return valid_video_id(video_id) and self.path_for(video_id).exists()

    def delete(self, video_id: str) -> None:
        self.path_for(video_id).unlink(missing_ok=True)

    def clean_tmp(self) -> None:
        """Remove what interrupted downloads left behind (crash, restart)."""
        if not self.tmp_dir.exists():
            return
        limit = time.time() - TMP_MAX_AGE
        for entry in self.tmp_dir.iterdir():
            if entry.stat().st_mtime < limit:
                shutil.rmtree(entry, ignore_errors=True)

    def download(
        self, video_id: str, worker: str, on_progress: ProgressCallback
    ) -> tuple[str, int]:
        """Blocking: run in a thread. Returns (title, size in bytes).

        Downloads into a folder of its own, then renames the finished file into
        the cache in one step: even if two replicas end up downloading the same
        video, the cache only ever holds complete files."""
        target = self.path_for(video_id)
        work = self.tmp_dir / f"{worker}-{video_id}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            title = self._fetch(video_id, work, on_progress)
            done = work / f"{video_id}.mp4"
            if not done.exists():
                msg = f"Download of {video_id} produced no mp4"
                raise RuntimeError(msg)
            os.replace(done, target)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        on_progress(1.0)
        return title, target.stat().st_size

    def _fetch(self, video_id: str, work: Path, on_progress: ProgressCallback) -> str:
        h = self.max_height

        def hook(d: dict[str, Any]) -> None:
            if d.get("status") != "downloading":
                return
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if not total:
                return
            ratio = min(d.get("downloaded_bytes", 0) / total, 1.0)
            # Video and audio arrive as two downloads: map them onto one bar.
            if d.get("info_dict", {}).get("vcodec", "none") == "none":
                on_progress(0.9 + 0.09 * ratio)
            else:
                on_progress(0.9 * ratio)

        opts: dict[str, Any] = {
            # H.264 + AAC in MP4 plays in every browser, including old smart TVs.
            "format": (
                f"bv*[height<={h}][vcodec^=avc1]+ba[acodec^=mp4a]"
                f"/b[height<={h}][ext=mp4]"
                f"/bv*[height<={h}]+ba/b"
            ),
            "merge_output_format": "mp4",
            "outtmpl": str(work / f"{video_id}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [hook],
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(url, download=True)
        return (info or {}).get("title", "")
