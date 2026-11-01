import pytest

from karaoke.cancelled import cancel_reason


@pytest.mark.parametrize(
    "texts",
    [
        ("Michel Sardou",),
        ("MICHEL SARDOU",),
        ("Michel Sardou & Garou",),
        ("", "Les lacs du Connemara - Michel Sardou | Karaoke Version | KaraFun"),
    ],
)
def test_cancelled_artist_is_flagged(texts: tuple[str, ...]) -> None:
    reason = cancel_reason(*texts)
    assert reason is not None
    assert reason.startswith("Michel Sardou")


@pytest.mark.parametrize(
    "texts",
    [
        ("Renaud",),
        ("Michel Sardounet",),  # whole words only
        ("Michel Polnareff", "Sardine"),
        ("",),
    ],
)
def test_other_artists_are_not_flagged(texts: tuple[str, ...]) -> None:
    assert cancel_reason(*texts) is None
