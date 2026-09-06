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
    source: str
    track_id: str
    url: str
    title: str | None

    @property
    def key(self) -> str:
        """Registry key. Namespaced because track IDs are only unique per source."""
        return f"{self.source}:{self.track_id}"

    @property
    def label(self) -> str:
        """What to show the user before the real title is known."""
        return self.title or self.url


@dataclass
class DownloadedTrack:
    path: str
    title: str


class DownloadRegistry:
    def __init__(self, download_dir: str) -> None:
        self.path = Path(download_dir) / "downloads.json"
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        self._data = json.loads(self.path.read_text())
        if self._migrate_legacy_keys():
            self._save()

    def _migrate_legacy_keys(self) -> bool:
        """Keys used to be bare YouTube video IDs, before other sources existed."""
        legacy = [key for key in self._data if ":" not in key]
        for key in legacy:
            self._data[f"youtube:{key}"] = self._data.pop(key)
        if legacy:
            logger.info("Migrated %d legacy registry key(s)", len(legacy))
        return bool(legacy)

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    def check(self, key: str) -> str | None:
        """Return the title if already downloaded and file exists, else None."""
        entry = self._data.get(key)
        if entry is None:
            return None
        if Path(entry["filename"]).exists():
            return entry["title"]
        # File was deleted, remove stale entry
        logger.info("File missing for %s, removing stale registry entry", key)
        del self._data[key]
        self._save()
        return None

    def register(self, key: str, filename: str, title: str) -> None:
        self._data[key] = {"filename": filename, "title": title}
        self._save()


def _track_from_entry(entry: dict, fallback_url: str | None = None) -> TrackInfo | None:
    """Build a TrackInfo from a yt-dlp info dict, flat or fully resolved.

    Flat playlist entries carry the permalink in `url` and the source in
    `ie_key`; fully resolved ones carry them in `webpage_url` and
    `extractor_key`, with `url` holding an expiring media stream instead, hence
    the ordering below. SoundCloud sets give no title in flat mode, so `title`
    stays None until the track is actually downloaded.
    """
    track_id = entry.get("id")
    url = entry.get("webpage_url") or entry.get("url") or fallback_url
    if not track_id or not url:
        return None

    source = entry.get("ie_key") or entry.get("extractor_key") or entry.get("extractor")
    return TrackInfo(
        source=str(source).lower() if source else "unknown",
        track_id=str(track_id),
        url=url,
        title=entry.get("title"),
    )


def extract_info(url: str) -> list[TrackInfo]:
    """Extract track info without downloading. Supports playlists and sets.

    Uses flat extraction for speed: only fetches IDs and permalinks from the
    playlist page without resolving each track individually.
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

    # Single track: no "entries" key
    if "entries" not in info:
        track = _track_from_entry(info, fallback_url=url)
        return [track] if track else []

    tracks = [
        _track_from_entry(entry) for entry in info["entries"] if entry is not None
    ]
    return [track for track in tracks if track is not None]


def download_single(url: str, download_dir: str) -> DownloadedTrack:
    """Download a single track's audio. Returns the output file and its title."""
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
            # No-ops on sources SponsorBlock doesn't cover, such as SoundCloud
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
        # Fragmented (HLS) downloads, as SoundCloud serves, print a progress
        # bar even under `quiet`
        "noprogress": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

        if info is None:
            msg = f"Failed to download {url}"
            raise RuntimeError(msg)

        mp3_path = str(Path(ydl.prepare_filename(info)).with_suffix(".mp3"))

    # FFmpegMetadata doesn't reliably write the album tag from parse_metadata,
    # so we set it directly via mutagen
    uploader = info.get("uploader", "")
    if uploader:
        _set_album_tag(mp3_path, uploader)

    return DownloadedTrack(path=mp3_path, title=info.get("title") or "Unknown")


def _set_album_tag(filepath: str, album: str) -> None:
    m = MP3(filepath)
    if m.tags is None:
        m.add_tags()
    if m.tags is not None:
        m.tags.add(TALB(encoding=3, text=album))
        m.save()


def download_with_retry(url: str, download_dir: str) -> DownloadedTrack:
    """Download with retries for transient failures."""
    last_error = RuntimeError(f"All {MAX_RETRIES} attempts failed for {url}")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return download_single(url, download_dir)
        except Exception as e:
            last_error = e
            logger.warning(
                "Attempt %d/%d failed for %s: %s", attempt, MAX_RETRIES, url, e
            )
    raise last_error
