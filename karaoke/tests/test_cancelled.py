import pytest

from karaoke.cancelled import Cancellation, find, refusal, split_artist
from karaoke.views import main_artist

SARDOU = Cancellation("Michel Sardou", "Cancel par la maison.")


@pytest.mark.parametrize(
    ("artist", "title"),
    [
        ("Michel Sardou", "Les lacs du Connemara"),
        ("MICHEL SARDOU", ""),
        ("Michel Sardou & Garou", "La rivière de notre enfance"),
        ("Garou feat. Michel Sardou", "Duo"),
        # YouTube result: no parsed artist, the title is searched
        ("", "Les lacs du Connemara - Michel Sardou | Karaoke Version | KaraFun"),
        # Popular list / YouTube: split_title() swaps song and artist
        ("Les Lacs du Connemara (Karaoke)", "Michel Sardou"),
        ("La maladie d'amour | Paroles", "Michel Sardou"),
        # Rows queued before the deploy: the channel as artist
        ("Sing King", "Michel Sardou - La maladie d'amour | Paroles"),
    ],
)
def test_cancelled_artist_is_flagged(artist: str, title: str) -> None:
    assert find([SARDOU], artist, title) == SARDOU


@pytest.mark.parametrize(
    ("artist", "title"),
    [
        ("Renaud", "Mistral gagnant"),
        ("Michel Sardounet", ""),  # whole words only
        ("Michel Polnareff", "Sardine"),
        ("", ""),
    ],
)
def test_other_artists_are_not_flagged(artist: str, title: str) -> None:
    assert find([SARDOU], artist, title) is None


@pytest.mark.parametrize(
    ("name", "artist"),
    [
        ("Michel", "Michel Polnareff"),
        ("Christophe", "Christophe Maé"),
        ("Patrick", "Patrick Bruel"),
        ("Love", "Courtney Love"),
    ],
)
def test_a_name_matches_whole_artists_not_some_of_their_words(
    name: str, artist: str
) -> None:
    assert find([Cancellation(name, "x")], artist, "Une chanson") is None


LOVE = Cancellation("Love", "Trop de love songs", "Tom")


@pytest.mark.parametrize(
    ("artist", "title"),
    [
        ("Céline Dion", "All by myself love"),
        ("The Beatles", "All you need is love"),
        ("", "Crazy in love - Beyoncé karaoke"),  # one word: titles not searched
    ],
)
def test_a_one_word_name_never_flags_titles(artist: str, title: str) -> None:
    assert find([LOVE], artist, title) is None


def test_a_one_word_name_flags_that_artist_alone_or_in_a_duet() -> None:
    assert find([LOVE], "Love", "Alone again or") == LOVE
    assert find([LOVE], "Arthur Lee & Love", "Seven and seven is") == LOVE


def test_the_oldest_matching_cancellation_wins() -> None:
    garou = Cancellation("Garou", "y")
    assert find([SARDOU, garou], "Michel Sardou & Garou", "") == SARDOU
    assert find([garou, SARDOU], "Michel Sardou & Garou", "") == garou


@pytest.mark.parametrize(
    ("credit", "parts"),
    [
        ("Michel Sardou & Garou", ["Michel Sardou", "Garou"]),
        ("A, B feat. C ft D", ["A", "B", "C", "D"]),
        ("Orelsan x Stromae", ["Orelsan", "Stromae"]),
        ("Les Rita Mitsouko", ["Les Rita Mitsouko"]),
        ("Featherstone", ["Featherstone"]),
        ("", []),
    ],
)
def test_split_artist(credit: str, parts: list[str]) -> None:
    assert split_artist(credit) == parts


def test_the_sheet_is_prefilled_with_the_first_artist() -> None:
    assert main_artist("Michel Sardou & Garou") == "Michel Sardou"
    assert main_artist("") == ""


def test_the_key_ignores_case_accents_and_punctuation() -> None:
    assert Cancellation("  Céline   DION!", "x").key == "celine dion"


@pytest.mark.parametrize(
    ("artist", "reason"),
    [
        ("", "relou"),
        ("  !! ", "relou"),  # nothing left once normalized
        ("Michel Sardou", ""),
    ],
)
def test_a_cancellation_needs_a_name_and_a_reason(artist: str, reason: str) -> None:
    assert refusal(artist, reason)


def test_a_real_artist_with_a_reason_is_fine() -> None:
    assert refusal("The Beatles", "Trop mainstream") is None
    assert refusal("Jo", "relou") is None  # short names are the guests' call


def test_label_names_who_cancelled_and_ends_a_sentence() -> None:
    assert SARDOU.label() == "Michel Sardou : Cancel par la maison."
    lea = Cancellation("Indochine", "trop triste", "Léa", 0)
    assert lea.label() == "Indochine : trop triste (cancel par Léa)."
    assert lea.details()["by"] == "Léa"
