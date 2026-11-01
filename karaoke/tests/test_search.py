import pytest

from karaoke.search import SearchResult, normalize, rank, split_title


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
