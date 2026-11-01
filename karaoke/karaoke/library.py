"""Video cache on disk: download with yt-dlp, evict the least recently played."""

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]


class Library:
    def __init__(self, songs_dir: Path, max_height: int, max_bytes: int) -> None:
        self.dir = songs_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_height = max_height
        self.max_bytes = max_bytes
        self._index_path = self.dir / "index.json"
        self._lock = threading.Lock()
        self._index: dict[str, dict[str, Any]] = {}
        if self._index_path.exists():
            self._index = json.loads(self._index_path.read_text())

    def _save(self) -> None:
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2, ensure_ascii=False))
        tmp.replace(self._index_path)

    def path_for(self, video_id: str) -> Path:
        return self.dir / f"{video_id}.mp4"

    def has(self, video_id: str) -> bool:
        return video_id in self._index and self.path_for(video_id).exists()

    def touch(self, video_id: str) -> None:
        with self._lock:
            if video_id in self._index:
                self._index[video_id]["last_used"] = time.time()
                self._save()

    def download(self, video_id: str, on_progress: ProgressCallback) -> Path:
        """Blocking: run in a thread. Returns the cached file path."""
        target = self.path_for(video_id)
        if self.has(video_id):
            self.touch(video_id)
            return target

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
            "outtmpl": str(self.dir / f"{video_id}.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [hook],
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(url, download=True)
        if not target.exists():
            msg = f"Download of {video_id} produced no mp4"
            raise RuntimeError(msg)

        with self._lock:
            self._index[video_id] = {
                "title": (info or {}).get("title", ""),
                "size": target.stat().st_size,
                "last_used": time.time(),
            }
            self._evict()
            self._save()
        on_progress(1.0)
        return target

    def _evict(self) -> None:
        total = sum(e["size"] for e in self._index.values())
        for vid, entry in sorted(self._index.items(), key=lambda i: i[1]["last_used"]):
            if total <= self.max_bytes:
                break
            logger.info("Evicting %s (%s) from cache", vid, entry.get("title"))
            self.path_for(vid).unlink(missing_ok=True)
            total -= entry["size"]
            del self._index[vid]
