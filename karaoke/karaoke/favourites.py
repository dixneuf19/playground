"""Favourite songs: one ❤️ list shared by every room, which anyone may edit.

Kept in Postgres with what a phone needs to show and queue the song (title,
artist, thumbnail), so the list renders without asking YouTube.
"""

from dataclasses import dataclass

from karaoke.cancelled import cancel_reason

MAX_FAVOURITES = 500  # stored; public site, so bounded
MAX_SHOWN = 50  # the latest ones, on the phone's search tab
# Share of KARAOKE_CACHE_MAX_GB past which other videos are evicted;
# favourites are evicted only past the whole of it.
NON_FAVOURITE_SHARE = 0.8


@dataclass(frozen=True)
class Favourite:
    video_id: str
    title: str
    artist: str
    thumbnail: str
    ready: bool = False  # in the video cache right now

    @property
    def cancelled(self) -> str | None:
        return cancel_reason(self.artist, self.title)
