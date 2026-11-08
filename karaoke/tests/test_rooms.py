from karaoke.photos import Photo
from karaoke.rooms import (
    CODE_ALPHABET,
    CODE_LENGTH,
    MAX_QUEUE,
    PHOTO_GAP_SECONDS,
    PHOTO_SECONDS,
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


def test_enqueue_starts_a_ready_song_when_idle_and_caps_the_queue() -> None:
    room = Room(code="ABCD")
    waiting = _item("waiting", "pending")
    assert room.enqueue(waiting)
    assert room.current is None  # nothing ready yet
    ready = _item("ready")
    assert room.enqueue(ready)
    assert room.current is ready
    room.queue = [_item(str(n), "pending") for n in range(MAX_QUEUE)]
    assert not room.enqueue(_item("one too many"))
    assert len(room.queue) == MAX_QUEUE


def test_play_pause_restart_need_a_song_and_keep_the_position() -> None:
    room = Room(code="ABCD")
    assert not room.pause(now=0)
    room.enqueue(_item("a"))
    room.report_position(10, now=100)
    assert room.pause(now=105)
    assert (room.state, room.played(now=500)) == ("paused", 15)
    assert room.play(now=500)
    assert room.played(now=502) == 17
    assert room.restart(now=600)
    assert room.played(now=600) == 0


def test_screen_report_follows_the_player() -> None:
    room = Room(code="ABCD")
    assert not room.screen_report("paused", 3)
    room.enqueue(_item("a"))
    assert room.screen_report("paused", 42.0, now=10)
    assert (room.state, room.played(now=99)) == ("paused", 42)


def test_photo_slot_shows_hides_then_shows_the_next() -> None:
    room = Room(code="ABCD")
    first, second = Photo(uploader="a"), Photo(uploader="b")
    room.photos.add(first)
    room.photos.add(second)
    assert room.rotate_photo(now=0)
    assert room.photo is first
    assert not room.rotate_photo(now=PHOTO_SECONDS - 1)  # still on screen
    assert room.rotate_photo(now=PHOTO_SECONDS)
    assert room.photo is None  # a short gap between photos
    assert room.rotate_photo(now=PHOTO_SECONDS + PHOTO_GAP_SECONDS)
    assert room.photo is second
    assert room.skip_photo(now=PHOTO_SECONDS + 3)
    assert not room.skip_photo(now=PHOTO_SECONDS + 3)
    assert room.rotate_photo(now=100)  # nothing left: the slot goes quiet
    assert room.photo_until is None


def test_position_of_counts_the_current_song() -> None:
    room = Room(code="ABCD")
    room.enqueue(QueueItem.new("aaaaaaaaaaa", "A", "", "Léa", "ready"))
    room.enqueue(QueueItem.new("bbbbbbbbbbb", "B", "", "Tom"))
    assert room.position_of("aaaaaaaaaaa", "Léa") == 1
    assert room.position_of("bbbbbbbbbbb", "Tom") == 2
    assert room.position_of("bbbbbbbbbbb", "Léa") is None


def test_new_item_builds_its_thumbnail_and_flags() -> None:
    item = QueueItem.new("abcdefghijk", "Les lacs du Connemara", "Michel Sardou", "")
    assert item.thumbnail == "https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg"
    assert item.singer == "Anonyme"
    assert item.cancelled
