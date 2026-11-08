"""Song search: curated karaoke channels first, all of YouTube on request."""

import asyncio
import difflib
import logging
import re
import time
import unicodedata
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import quote

import yt_dlp

logger = logging.getLogger(__name__)

CHANNEL_RESULTS = 20
YOUTUBE_RESULTS = 12


@dataclass(frozen=True)
class Channel:
    handle: str  # youtube.com/@<handle>
    label: str  # short name on the phone's result cards
    # Its title formats, with named song/artist groups.
    formats: tuple[re.Pattern[str], ...]


def _formats(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.I) for p in patterns)


# The curated channels, searched in parallel, in priority order: when several
# have the same song, or two results score the same, the earlier channel wins.
CHANNELS = (
    Channel(
        "karafunfr",
        "KaraFun FR",
        # "Karaoké Song - Artist *"
        _formats(r"^\s*karaok[eé]\s+(?P<song>.+) - (?P<artist>[^|]+?)[\s*]*$"),
    ),
    Channel(
        "karafun",
        "KaraFun",
        # "Song - Artist | Karaoke Version | KaraFun"
        _formats(
            r"^(?P<song>.+) - (?P<artist>.+?)\s*\|\s*karaoke version\s*\|\s*karafun$"
        ),
    ),
    # French songs missing from KaraFun's YouTube catalogue.
    Channel(
        "bdfabkaraoke9959",
        "BDFab",
        # "Artist - Song (chœurs) (2013) [BDFab karaoke]"
        _formats(
            r"^(?P<artist>.+?) - (?P<song>.+?)(?:\s*\(ch(?:œ|oe)urs\))?"
            r"(?:\s*\(\d{4}\))?\s*\[bdfab karaoke\]$"
        ),
    ),
    # English songs missing from KaraFun's YouTube catalogue.
    Channel(
        "singkingkaraoke",
        "Sing King",
        _formats(
            # "Artist - Song (Karaoke Version)", like most labels
            r"^(?P<artist>.+?) - (?P<song>.+?)\s*\(karaoke version\)$",
            # Variants, kept in the song: "Artist - Song (Piano Karaoke)"
            r"^(?P<artist>.+?) - (?P<song>.+\([^()]*\bkaraoke\b[^()]*\))$",
        ),
    ),
)
CHANNEL_HANDLES = tuple(c.handle for c in CHANNELS)
CHANNEL_LABELS = {c.handle: c.label for c in CHANNELS}


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
    # The curated channel's short name; YouTube results show `channel`.
    channel_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_title(raw: str) -> tuple[str, str] | None:
    """(song, artist) when the title is in a curated channel's format."""
    for fmt in (f for c in CHANNELS for f in c.formats):
        if m := fmt.match(raw.strip()):
            return m["song"].strip(), m["artist"].strip()
    return None


def split_title(raw: str) -> tuple[str, str]:
    """Turn a karaoke video title into (song, artist); best effort otherwise."""
    if parsed := parse_title(raw):
        return parsed
    cleaned = raw.rstrip(" *").strip()
    if " - " in cleaned:
        song, artist = cleaned.rsplit(" - ", 1)
        return song.strip(), artist.strip()
    return cleaned, ""


def is_karaoke(raw: str) -> bool:
    """Curated channels also post compilations without lyrics, teasers, and
    versions that keep the singer's voice ("Karaoke with Guide Vocal")."""
    text = normalize(raw)
    return "karaok" in text.replace(" ", "") and "guide vocal" not in text


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
    """Best match first. A song several channels have keeps the video of the
    first channel in `results` order (CHANNELS order)."""
    seen: set[str | tuple[str, str]] = set()
    unique = []
    for r in results:
        song = (normalize(r.title), normalize(r.artist))
        if r.video_id not in seen and song not in seen:
            seen.update((r.video_id, song))
            unique.append(r)
    return sorted(unique, key=lambda r: score(query, r), reverse=True)


def _to_result(
    entry: dict[str, Any], curated: bool, channel_label: str = ""
) -> SearchResult | None:
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
        channel_label=channel_label,
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


def search_channels(query: str, channels: Sequence[str]) -> list[SearchResult]:
    def one(handle: str) -> list[SearchResult]:
        url = f"https://www.youtube.com/@{handle}/search?query={quote(query)}"
        try:
            entries = _extract(url, CHANNEL_RESULTS)
        except Exception:
            logger.exception("Search failed on @%s", handle)
            return []
        return [
            r
            for e in entries
            if (r := _to_result(e, True, CHANNEL_LABELS.get(handle, handle)))
            and is_karaoke(r.raw_title)
        ]

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

    def __init__(self, channels: Sequence[str] = CHANNEL_HANDLES) -> None:
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

    async def describe(self, video_id: str) -> SearchResult | None:
        """What YouTube says a video is: from a recent search when one
        showed it (titles already parsed), otherwise looked up by id."""
        now = time.monotonic()
        for expires, results in self._cache.values():
            for r in results:
                if r.video_id == video_id and expires > now:
                    return r
        found = await self._search(video_id, False, video_id)
        if not found:
            return None
        r = found[0]
        if parsed := parse_title(r.raw_title):  # a link lookup is not parsed
            r = replace(r, title=parsed[0], artist=parsed[1])
        return r

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
