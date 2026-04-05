import json
import logging
import os
import re
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{11})"
)


def extract_video_id(url: str) -> str | None:
    m = YOUTUBE_URL_RE.search(url)
    return m.group(1) if m else None


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


def download_audio(url: str, download_dir: str) -> tuple[str, str]:
    """Download audio from URL. Returns (filename, title)."""
    os.makedirs(download_dir, exist_ok=True)

    info: dict = {}

    def _progress_hook(d: dict) -> None:
        nonlocal info
        if d["status"] == "finished":
            info = d.get("info_dict", {})

    opts = {
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
        "progress_hooks": [_progress_hook],
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    title = info.get("title", "Unknown")
    # After postprocessing the extension is mp3
    filename = os.path.join(download_dir, f"{title}.mp3")
    return filename, title
