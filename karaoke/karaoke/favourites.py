"""Favourite songs: one ❤️ list shared by every room, which anyone may edit.

Kept in Postgres with what a phone needs to show and queue the song (title,
artist, thumbnail), so the list renders without asking YouTube. Trust, then
verify: a heart is saved at once with the phone's title, and the leader
replaces it with YouTube's (worker.check_favourites).
"""

from dataclasses import dataclass

from karaoke.cancelled import Cancellation

MAX_FAVOURITES = 500  # stored; public site, so bounded
# Share of KARAOKE_CACHE_MAX_GB past which other videos are evicted;
# favourites are evicted only past the whole of it.
NON_FAVOURITE_SHARE = 0.8
CHECKS_PER_TICK = 3  # yt-dlp lookups from the home IP: keep them few
RECHECK_SECONDS = 600  # after a check YouTube didn't answer
MAX_FAILED_CHECKS = 3  # then the favourite is dropped


@dataclass(frozen=True)
class Favourite:
    video_id: str
    title: str
    artist: str
    thumbnail: str
    ready: bool = False  # in the video cache right now
    plays: int = 0  # rooms that sang it lately, as in the popular list
    # Set by the store from the cancelled artists table, like queued songs.
    cancelled: Cancellation | None = None
