"""Song search: curated karaoke channels first, all of YouTube on request."""

import difflib
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote

import yt_dlp

logger = logging.getLogger(__name__)

CHANNEL_RESULTS = 20
YOUTUBE_RESULTS = 12

# "Song - Artist | Karaoke Version | KaraFun" (international channel)
_INTL_SUFFIX = re.compile(r"\s*\|\s*karaoke version\s*\|\s*karafun\s*$", re.I)
# "Karaoké Song - Artist *" (French channel)
_FR_PREFIX = re.compile(r"^\s*karaok[eé]\s+", re.I)


@dataclass
class SearchResult:
    video_id: str
    title: str
    artist: str
    raw_title: str
    channel: str
    duration: int | None
    thumbnail: str
    curated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def split_title(raw: str) -> tuple[str, str]:
    """Turn a KaraFun video title into (song, artist); best effort otherwise."""
    cleaned = _FR_PREFIX.sub("", _INTL_SUFFIX.sub("", raw)).rstrip(" *").strip()
    if " - " in cleaned:
        song, artist = cleaned.rsplit(" - ", 1)
        return song.strip(), artist.strip()
    return cleaned, ""


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def score(query: str, result: SearchResult) -> float:
    """Word overlap plus fuzzy similarity, against 'song artist'."""
    q = normalize(query)
    target = normalize(f"{result.title} {result.artist}")
    qwords = set(q.split())
    overlap = len(qwords & set(target.split())) / max(len(qwords), 1)
    return overlap + difflib.SequenceMatcher(None, q, target).ratio()


def rank(query: str, results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    unique = []
    for r in results:
        if r.video_id not in seen:
            seen.add(r.video_id)
            unique.append(r)
    return sorted(unique, key=lambda r: score(query, r), reverse=True)


def _to_result(entry: dict[str, Any], curated: bool) -> SearchResult | None:
    video_id = entry.get("id")
    raw = entry.get("title")
    if not video_id or not raw or entry.get("ie_key") not in (None, "Youtube"):
        return None
    song, artist = split_title(raw) if curated else (raw, "")
    duration = entry.get("duration")
    return SearchResult(
        video_id=video_id,
        title=song,
        artist=artist,
        raw_title=raw,
        channel=entry.get("channel") or entry.get("uploader") or "",
        duration=int(duration) if isinstance(duration, int | float) else None,
        thumbnail=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        curated=curated,
    )


def _extract(url: str, limit: int) -> list[dict[str, Any]]:
    opts = {
        "extract_flat": "in_playlist",
        "playlistend": limit,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
        info = ydl.extract_info(url, download=False)
    return [e for e in (info or {}).get("entries") or [] if e]


def search_channels(query: str, channels: list[str]) -> list[SearchResult]:
    def one(handle: str) -> list[SearchResult]:
        url = f"https://www.youtube.com/@{handle}/search?query={quote(query)}"
        try:
            entries = _extract(url, CHANNEL_RESULTS)
        except Exception:
            logger.exception("Search failed on @%s", handle)
            return []
        return [r for e in entries if (r := _to_result(e, curated=True))]

    with ThreadPoolExecutor(max(len(channels), 1)) as pool:
        batches = list(pool.map(one, channels))
    return rank(query, [r for batch in batches for r in batch])


def search_youtube(query: str) -> list[SearchResult]:
    entries = _extract(f"ytsearch{YOUTUBE_RESULTS}:{query}", YOUTUBE_RESULTS)
    return [r for e in entries if (r := _to_result(e, curated=False))]
