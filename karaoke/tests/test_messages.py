import pytest
from pydantic import ValidationError

from karaoke.messages import Add, Ended, Move, Volume, parse


def test_add_trims_free_text_and_ignores_extra_fields() -> None:
    msg = parse(
        {
            "type": "add",
            "video_id": "abcdefghijk",
            "title": "  x" * 200,
            "singer": None,
            "cancelled": None,  # the phone has no say on this
            "HEADERS": {"HX-Request": "true"},  # sent by htmx
        }
    )
    assert isinstance(msg, Add)
    assert len(msg.title) == 200
    assert msg.singer == ""


@pytest.mark.parametrize(
    "video_id", ["../../etc/passwd", "abc", 42, None, "abcdefghijkl"]
)
def test_add_rejects_anything_but_a_youtube_id(video_id: object) -> None:
    with pytest.raises(ValidationError):
        parse({"type": "add", "video_id": video_id})


def test_form_strings_are_coerced() -> None:
    assert parse({"type": "move", "item_id": "x", "index": "3"}) == Move(
        type="move", item_id="x", index=3
    )
    assert parse({"type": "volume", "value": "0.4"}) == Volume(type="volume", value=0.4)


@pytest.mark.parametrize(
    "data",
    [
        {"type": "nope"},
        {"type": "remove"},  # no item
        {"type": "volume", "value": float("nan")},
        {"type": "position", "item_id": "x", "t": float("inf")},
        {"type": "player", "state": "idle"},
    ],
)
def test_nonsense_is_rejected(data: dict) -> None:
    with pytest.raises(ValidationError):
        parse(data)


def test_screen_only_messages() -> None:
    assert parse({"type": "ended", "item_id": "x"}).screen_only
    assert Ended.screen_only and not Volume.screen_only
