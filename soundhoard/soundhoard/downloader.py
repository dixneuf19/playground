import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp

logger = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    video_id: str
    title: str
    filename: str


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
    """Extract video info without downloading. Supports playlists."""
    opts = {
        "extract_flat": False,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if info is None:
        return []

    entries = info.get("entries", [info])
    return [
        TrackInfo(
            video_id=entry["id"],
            title=entry.get("title", "Unknown"),
            filename="",  # filled after download
        )
        for entry in entries
        if entry is not None
    ]


def download_audio(url: str, download_dir: str, video_id: str | None = None) -> str:
    """Download audio from URL. Returns the output filename.

    If video_id is provided, only download that specific video (useful for
    skipping already-downloaded entries in a playlist).
    """
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
        "parse_metadata": ["%(uploader)s:%(artist)s"],
        "quiet": True,
        "no_warnings": True,
    }

    if video_id:
        opts["match_filter"] = lambda info_dict: (
            None if info_dict.get("id") == video_id else "Skipping"
        )

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    if info is None:
        msg = f"Failed to download {url}"
        raise RuntimeError(msg)

    # For single video, info is the video dict; for filtered playlist, find our entry
    if info.get("id") == video_id or video_id is None:
        title = info.get("title", "Unknown")
    else:
        entries = info.get("entries", [])
        downloaded = [e for e in entries if e is not None and e.get("id") == video_id]
        title = downloaded[0].get("title", "Unknown") if downloaded else "Unknown"

    return os.path.join(download_dir, f"{title}.mp3")
