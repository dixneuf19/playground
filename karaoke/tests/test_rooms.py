from karaoke.rooms import (
    CODE_ALPHABET,
    CODE_LENGTH,
    ItemStatus,
    QueueItem,
    Room,
    generate_code,
)


def _item(name: str, status: ItemStatus = "ready") -> QueueItem:
    item = QueueItem(video_id=name, title=name, artist="", singer="me", thumbnail="")
    item.status = status
    return item


def test_codes_are_unambiguous() -> None:
    for _ in range(50):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert set(code) <= set(CODE_ALPHABET)


def test_advance_skips_songs_still_downloading() -> None:
    room = Room(code="ABCD")
    slow, fast = _item("slow", "downloading"), _item("fast")
    room.add(slow)
    room.add(fast)

    assert room.advance() is fast
    assert room.state == "playing"
    assert room.queue == [slow]  # keeps its place at the head


def test_finish_is_idempotent_across_screens() -> None:
    room = Room(code="ABCD")
    first, second = _item("one"), _item("two")
    room.add(first)
    room.add(second)
    room.advance()

    assert room.finish(first.id)
    assert room.current is second
    assert not room.finish(first.id)  # a second screen reporting the same end
    assert room.current is second
    assert room.sung == 1


def test_advance_on_empty_queue_goes_idle() -> None:
    room = Room(code="ABCD")
    room.add(_item("one"))
    room.advance()
    room.advance()
    assert room.current is None
    assert room.state == "idle"


def test_move_clamps_index() -> None:
    room = Room(code="ABCD")
    a, b, c = _item("a"), _item("b"), _item("c")
    for i in (a, b, c):
        room.add(i)

    assert room.move(c.id, 0)
    assert room.queue == [c, a, b]
    assert room.move(c.id, 99)
    assert room.queue == [a, b, c]
    assert not room.move("nope", 0)


def test_remove() -> None:
    room = Room(code="ABCD")
    a = _item("a")
    room.add(a)
    assert room.remove(a.id)
    assert not room.remove(a.id)


def test_position_runs_while_playing_and_freezes_on_pause() -> None:
    room = Room(code="ABCD", queue=[_item("a"), _item("b")])
    room.advance()
    assert room.played() == 0
    room.report_position(30, now=1000)
    assert room.played(now=1010) == 40
    room.report_position(room.played(now=1010), now=1010)
    room.state = "paused"
    assert room.played(now=2000) == 40
    room.advance()
    assert room.played(now=2000) == 0
