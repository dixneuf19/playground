"""Cancelled artists: their songs stay playable, but with a warning.

Any guest cancels or uncancels an artist (stored in Postgres, global to every
room, see karaoke.db). Matching ignores case, accents and punctuation:

- a name matches an artist's whole name, or one of its parts ("Michel Sardou"
  catches "Michel Sardou & Garou", "Michel" doesn't catch Michel Polnareff);
- a name of 2+ words also matches as whole words anywhere in the artist and
  the title, for songs whose artist is missing or mis-parsed (YouTube results,
  the popular list, "Michel Sardou - Les lacs du Connemara (Karaoke)").
  One-word names never search titles: cancelling "Love" flags no love song.
"""

import re
import time
from dataclasses import dataclass, field

from karaoke.search import normalize

MONTHS = [
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
]
# Between the artists of a duet: "A & B", "A, B", "A feat. B", "A x B", "A et B"
_SEPARATORS = re.compile(r"\s*(?:[&,/+]|\b(?:feat|ft)\b\.?|\s(?:x|et)\s)\s*", re.I)


def artist_key(artist: str) -> str:
    return normalize(artist)


def split_artist(artist: str) -> list[str]:
    """The artists of a credit, as written: "A & B feat. C" -> [A, B, C]."""
    return [p.strip() for p in _SEPARATORS.split(artist) if p.strip()]


def refusal(artist: str, reason: str) -> str | None:
    """Why this cancellation can't be stored (in French), or None."""
    if not artist_key(artist):
        return "Donne le nom de l'artiste"
    if not reason:
        return "Donne une raison"
    return None


@dataclass(frozen=True)
class Cancellation:
    artist: str  # as typed by whoever cancelled
    reason: str
    by: str = ""  # their name in the room, "" when unknown
    at: float = 0.0  # epoch
    key: str = field(init=False, compare=False)  # normalized once per row

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", artist_key(self.artist))

    def date(self) -> str:
        """ "8 nov.", in the server's local time."""
        t = time.localtime(self.at)
        return f"{t.tm_mday} {MONTHS[t.tm_mon - 1]}"

    def label(self) -> str:
        """One line for the big screen and the confirmation sheet."""
        by = f" (cancel par {self.by})" if self.by else ""
        text = f"{self.artist} : {self.reason}{by}"
        return text if text.endswith((".", "!", "?", "…")) else f"{text}."

    def details(self) -> dict[str, str]:
        """For the phone's artist sheet."""
        return {
            "artist": self.artist,
            "reason": self.reason,
            "by": self.by,
            "date": self.date(),
        }


def find(
    cancellations: list[Cancellation], artist: str, title: str
) -> Cancellation | None:
    """The first (oldest) cancellation naming this song's artist."""
    whole = artist_key(artist)
    parts = {whole, *map(artist_key, split_artist(artist))}
    words = f" {whole} {artist_key(title)} "
    for c in cancellations:
        if c.key in parts or (" " in c.key and f" {c.key} " in words):
            return c
    return None
