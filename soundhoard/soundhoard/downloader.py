import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from mutagen.id3 import TALB
from mutagen.mp3 import MP3

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


@dataclass
class TrackInfo:
    video_id: str
    title: str


class DownloadRegistry:
    def __init__(self, download_dir: str) -> None:
        self.path = Path(download_dir) / "downloads.json"
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            self._data = json.loads(self.path.read_text())

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    def check(self, video_id: str) -> str | None:
        """Return the title if already downloaded and file exists, else None."""
        entry = self._data.get(video_id)
        if entry is None:
            return None
        if Path(entry["filename"]).exists():
            return entry["title"]
        # File was deleted — remove stale entry
        logger.info("File missing for %s, removing stale registry entry", video_id)
        del self._data[video_id]
        self._save()
        return None

    def register(self, video_id: str, filename: str, title: str) -> None:
        self._data[video_id] = {"filename": filename, "title": title}
        self._save()


def extract_info(url: str) -> list[TrackInfo]:
    """Extract video info without downloading. Supports playlists.

    Uses flat extraction for speed — only fetches video IDs and titles
    from the playlist page without resolving each video individually.
    """
    opts = {
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        return []

    # Single video: no "entries" key
    if "entries" not in info:
        video_id = info.get("id")
        if not video_id:
            return []
        return [TrackInfo(video_id=video_id, title=info.get("title", "Unknown"))]

    return [
        TrackInfo(
            video_id=entry["id"],
            title=entry.get("title", "Unknown"),
        )
        for entry in info["entries"]
        if entry is not None and entry.get("id")
    ]


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def download_single(video_id: str, download_dir: str) -> str:
    """Download a single video's audio. Returns the output filename."""
    os.makedirs(download_dir, exist_ok=True)

    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
            {"key": "SponsorBlock", "categories": ["sponsor"]},
            {"key": "ModifyChapters", "remove_sponsor_segments": ["sponsor"]},
        ],
        "writethumbnail": True,
        "outtmpl": os.path.join(download_dir, "%(title)s.%(ext)s"),
        "parse_metadata": [
            "%(uploader)s:%(artist)s",
        ],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url(video_id), download=True)

        if info is None:
            msg = f"Failed to download {video_id}"
            raise RuntimeError(msg)

        mp3_path = str(Path(ydl.prepare_filename(info)).with_suffix(".mp3"))

    # FFmpegMetadata doesn't reliably write the album tag from parse_metadata,
    # so we set it directly via mutagen
    uploader = info.get("uploader", "")
    if uploader:
        _set_album_tag(mp3_path, uploader)

    return mp3_path


def _set_album_tag(filepath: str, album: str) -> None:
    m = MP3(filepath)
    if m.tags is None:
        m.add_tags()
    if m.tags is not None:
        m.tags.add(TALB(encoding=3, text=album))
        m.save()


def download_with_retry(video_id: str, download_dir: str) -> str:
    """Download with retries for transient failures."""
    last_error = RuntimeError(f"All {MAX_RETRIES} attempts failed for {video_id}")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return download_single(video_id, download_dir)
        except Exception as e:
            last_error = e
            logger.warning(
                "Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, video_id, e
            )
    raise last_error
