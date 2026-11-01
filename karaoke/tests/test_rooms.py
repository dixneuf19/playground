from karaoke.rooms import CODE_ALPHABET, ItemStatus, QueueItem, Room, RoomRegistry


def _item(name: str, status: ItemStatus = "ready") -> QueueItem:
    item = QueueItem(video_id=name, title=name, artist="", singer="me", thumbnail="")
    item.status = status
    return item


def test_codes_are_unambiguous() -> None:
    registry = RoomRegistry()
    for _ in range(50):
        room = registry.create()
        assert len(room.code) == 4
        assert set(room.code) <= set(CODE_ALPHABET)
        assert registry.get(room.code.lower()) is room


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
