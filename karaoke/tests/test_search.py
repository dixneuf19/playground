import asyncio

import pytest

from karaoke import search
from karaoke.search import (
    SearchResult,
    is_strong,
    normalize,
    rank,
    split_title,
    video_id_from_url,
    youtube_query,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Bohemian Rhapsody - Queen | Karaoke Version | KaraFun",
            ("Bohemian Rhapsody", "Queen"),
        ),
        ("Karaoké Manhattan-Kaboul - Renaud", ("Manhattan-Kaboul", "Renaud")),
        ("Karaoké Germaine - Renaud *", ("Germaine", "Renaud")),
        (
            "Je vais t'aimer - Michel Sardou | Karaoke Version | KaraFun",
            ("Je vais t'aimer", "Michel Sardou"),
        ),
        ("Some random video", ("Some random video", "")),
    ],
)
def test_split_title(raw: str, expected: tuple[str, str]) -> None:
    assert split_title(raw) == expected


def test_normalize_strips_accents_and_punctuation() -> None:
    assert normalize("Les Champs-Élysées !") == "les champs elysees"


def _result(video_id: str, title: str, artist: str) -> SearchResult:
    return SearchResult(
        video_id=video_id,
        title=title,
        artist=artist,
        raw_title=f"{title} - {artist}",
        channel="KaraFun",
        duration=None,
        thumbnail="",
        curated=True,
    )


def test_rank_puts_exact_match_first_and_dedupes() -> None:
    results = [
        _result("a", "Je viens du Sud", "Michel Sardou"),
        _result("b", "Les Champs-Élysées", "Joe Dassin"),
        _result("c", "Les lacs du Connemara", "Michel Sardou"),
        _result("c", "Les lacs du Connemara", "Michel Sardou"),
    ]
    ranked = rank("les lacs du connemara", results)
    assert [r.video_id for r in ranked] == ["c", "a", "b"] or ranked[0].video_id == "c"
    assert len(ranked) == 3


def test_rank_matches_artist_and_title() -> None:
    results = [
        _result("x", "Germaine", "Renaud"),
        _result("y", "Manhattan-Kaboul", "Renaud"),
    ]
    assert rank("renaud manhattan kaboul", results)[0].video_id == "y"


def test_is_strong_needs_every_query_word() -> None:
    assert is_strong("renaud manhattan", _result("y", "Manhattan-Kaboul", "Renaud"))
    assert not is_strong(
        "hotel california", _result("d", "Dani California", "Red Hot Chili Peppers")
    )


def test_youtube_query_asks_for_karaoke_versions() -> None:
    assert youtube_query("hotel california") == "hotel california karaoke"
    assert youtube_query("Hotel California karaoké") == "Hotel California karaoké"


def test_video_id_from_pasted_links() -> None:
    vid = "BHDc1pFFDz8"
    for link in (
        f"https://youtu.be/{vid}",
        f"https://youtu.be/{vid}?si=abc",
        f"https://www.youtube.com/watch?v={vid}&t=10",
        f"https://m.youtube.com/watch?feature=share&v={vid}",
        f"https://youtube.com/shorts/{vid}",
    ):
        assert video_id_from_url(link) == vid
    assert video_id_from_url("hotel california") is None


def _found(video_id: str, raw: str, channel: str, curated: bool) -> SearchResult:
    title, artist = split_title(raw) if curated else (raw, "")
    return SearchResult(video_id, title, artist, raw, channel, None, "t", curated)


def test_describe_uses_recent_searches_then_a_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = _found(
        "aaaaaaaaaaa", "Song - Artist | Karaoke Version | KaraFun", "KaraFun", True
    )
    lookups = {
        "bbbbbbbbbbb": _found(
            "bbbbbbbbbbb", "Karaoké Titre - Chanteur *", "KaraFun France", False
        ),
        "ccccccccccc": _found("ccccccccccc", "Some clip", "Someone", False),
    }
    monkeypatch.setattr(search, "search_channels", lambda _q, _c: [found])
    monkeypatch.setattr(
        search, "lookup_video", lambda v: [lookups[v]] if v in lookups else []
    )
    searcher = search.Searcher(["karafun"])

    async def main() -> None:
        await searcher.find("song", everywhere=False)
        assert await searcher.describe("aaaaaaaaaaa") == found  # from the search
        karafun = await searcher.describe("bbbbbbbbbbb")  # a lookup, parsed
        assert karafun and (karafun.title, karafun.artist) == ("Titre", "Chanteur")
        other = await searcher.describe("ccccccccccc")  # not KaraFun: kept whole
        assert other and (other.title, other.artist) == ("Some clip", "")
        assert await searcher.describe("zzzzzzzzzzz") is None

    asyncio.run(main())
