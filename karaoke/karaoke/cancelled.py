"""Artists the house has cancelled: their songs stay playable, but with a warning.

Add an entry per artist: display name -> reason shown to whoever picks the song.
Matching ignores case, accents and punctuation, on whole words, so
"Michel Sardou" also catches "Michel Sardou & Garou" but not "Sardoun".
"""

from karaoke.search import normalize

CANCELLED_ARTISTS: dict[str, str] = {
    "Michel Sardou": "Cancel par la maison.",
}


def cancel_reason(*texts: str) -> str | None:
    """Reason if any of the texts (artist, title...) names a cancelled artist."""
    haystacks = [f" {normalize(t)} " for t in texts if t]
    for artist, reason in CANCELLED_ARTISTS.items():
        needle = f" {normalize(artist)} "
        if any(needle in h for h in haystacks):
            return f"{artist} — {reason}"
    return None
