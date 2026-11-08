"""Song search: curated karaoke channels first, all of YouTube on request."""

import asyncio
import difflib
import logging
import re
import time
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


def is_strong(query: str, result: SearchResult) -> bool:
    """Every word of the query appears in the song or artist."""
    target = set(normalize(f"{result.title} {result.artist}").split())
    return set(normalize(query).split()) <= target


_VIDEO_ID = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/))"
    r"([A-Za-z0-9_-]{11})"
)


def video_id_from_url(text: str) -> str | None:
    """The video id when a YouTube link was pasted in the search box."""
    m = _VIDEO_ID.search(text)
    return m.group(1) if m else None


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


def youtube_query(query: str) -> str:
    """Bias towards karaoke versions: the plain query returns the clip."""
    return query if "karaok" in normalize(query) else f"{query} karaoke"


def search_youtube(query: str) -> list[SearchResult]:
    q = youtube_query(query)
    entries = _extract(f"ytsearch{YOUTUBE_RESULTS}:{q}", YOUTUBE_RESULTS)
    return [r for e in entries if (r := _to_result(e, curated=False))]


def lookup_video(video_id: str) -> list[SearchResult]:
    """A single pasted link, resolved without searching."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False
            )
    except Exception:
        logger.warning("Lookup failed for %s", video_id)
        return []
    if not info:
        return []
    r = _to_result({**info, "ie_key": "Youtube"}, curated=False)
    return [r] if r else []


MAX_CONCURRENT_SEARCHES = 4  # per replica; more requests wait their turn
SEARCH_CACHE_SECONDS = 600
SEARCH_CACHE_SIZE = 500


@dataclass
class SearchPage:
    query: str
    everywhere: bool
    results: list[SearchResult]
    # False: the catalogue has nothing matching every word, so the phone
    # offers YouTube up front.
    strong: bool


class Searcher:
    """yt-dlp searches, a few at a time per replica and cached for a while:
    hammering YouTube from the home IP could get downloads blocked."""

    def __init__(self, channels: list[str]) -> None:
        self.channels = channels
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)
        self._cache: dict[tuple[str, str], tuple[float, list[SearchResult]]] = {}
        self._warming: set[asyncio.Task[Any]] = set()

    async def find(self, query: str, everywhere: bool) -> SearchPage:
        q = query.strip()[:120]
        if not q:
            return SearchPage(q, everywhere, [], strong=False)
        link = video_id_from_url(q)
        results = await self._search(q, everywhere, link)
        strong = everywhere or bool(link) or any(is_strong(q, r) for r in results)
        if not strong:
            # The phone will likely ask YouTube next: get it ready.
            task = asyncio.create_task(self._search(q, True, None))
            self._warming.add(task)
            task.add_done_callback(self._warming.discard)
        return SearchPage(q, everywhere, results, strong)

    async def _search(
        self, q: str, everywhere: bool, link: str | None
    ) -> list[SearchResult]:
        key = ("link", link) if link else ("all" if everywhere else "cat", normalize(q))
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        async with self._slots:
            if link:
                results = await asyncio.to_thread(lookup_video, link)
            elif everywhere:
                results = await asyncio.to_thread(search_youtube, q)
            else:
                results = await asyncio.to_thread(search_channels, q, self.channels)
        if results:  # don't remember a failure
            if len(self._cache) >= SEARCH_CACHE_SIZE:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                if len(self._cache) >= SEARCH_CACHE_SIZE:
                    self._cache.clear()
            self._cache[key] = (now + SEARCH_CACHE_SECONDS, results)
        return results
