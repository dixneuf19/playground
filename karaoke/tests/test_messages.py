import pytest
from pydantic import ValidationError

from karaoke.messages import Add, Ended, Move, React, Volume, parse


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
    assert len(msg.title) <= 200 and msg.title == msg.title.strip()
    assert msg.singer == ""


@pytest.mark.parametrize(
    "video_id", ["../../etc/passwd", "abc", 42, None, "abcdefghijkl"]
)
def test_add_rejects_anything_but_a_youtube_id(video_id: object) -> None:
    with pytest.raises(ValidationError):
        parse({"type": "add", "video_id": video_id})


def test_names_never_end_with_a_space_after_cutting() -> None:
    msg = parse({"type": "add", "video_id": "abcdefghijk", "singer": "x" * 39 + " y"})
    assert isinstance(msg, Add)
    assert msg.singer == "x" * 39


def test_react_takes_a_known_kind_and_a_small_count() -> None:
    msg = parse({"type": "react", "kind": "fire", "count": "3"})
    assert isinstance(msg, React)
    assert (msg.kind, msg.count) == ("fire", 3)
    assert parse({"type": "react", "kind": "confetti"}) == React(
        type="react", kind="confetti", count=1
    )


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "<script>"},
        {"kind": "🔥"},
        {"kind": "fire", "count": 0},
        {"kind": "fire", "count": 11},
    ],
)
def test_react_rejects_anything_else(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse({"type": "react", **data})


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
