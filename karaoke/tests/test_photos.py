from karaoke.photos import MAX_PENDING_PER_UPLOADER, Photo, PhotoQueue


def _queue(*uploaders: str) -> tuple[PhotoQueue, list[Photo]]:
    q = PhotoQueue()
    photos = [Photo(uploader=u) for u in uploaders]
    for p in photos:
        q.add(p)
    return q, photos


def _drain(q: PhotoQueue) -> list[str]:
    order = []
    while (p := q.pop_next()) is not None:
        order.append(p.uploader)
    return order


def test_late_uploader_does_not_wait_behind_a_burst() -> None:
    q, _ = _queue(*["lea"] * 5, "tom")
    assert _drain(q) == ["lea", "tom", "lea", "lea", "lea", "lea"]


def test_round_robin_between_uploaders() -> None:
    q, _ = _queue("a", "a", "a", "b", "b", "c")
    assert _drain(q) == ["a", "b", "c", "a", "b", "a"]


def test_newcomer_goes_first_after_others_had_a_turn() -> None:
    q, _ = _queue("a", "a", "b", "b")
    assert [p.uploader for p in (q.pop_next(), q.pop_next()) if p] == ["a", "b"]
    q.add(Photo(uploader="c"))
    assert _drain(q) == ["c", "a", "b"]


def test_each_uploader_keeps_their_own_order() -> None:
    q, photos = _queue("a", "b", "a")
    shown = [q.pop_next() for _ in range(3)]
    assert [p.id for p in shown if p and p.uploader == "a"] == [
        photos[0].id,
        photos[2].id,
    ]


def test_upcoming_matches_real_order_without_consuming() -> None:
    q, _ = _queue("a", "a", "b")
    preview = [p.id for p in q.upcoming()]
    assert len(q.pending) == 3
    assert preview == [p.id for p in iter(q.pop_next, None)]


def test_only_uploader_can_cancel() -> None:
    q, photos = _queue("a", "b")
    assert q.remove(photos[0].id, "b") is None
    assert q.remove(photos[0].id, "a") is photos[0]
    assert [p.uploader for p in q.pending] == ["b"]


def test_pending_limit_per_uploader() -> None:
    q, _ = _queue(*["a"] * MAX_PENDING_PER_UPLOADER)
    assert not q.can_add("a")
    assert q.can_add("b")


def test_snapshot_is_anonymous() -> None:
    assert Photo(uploader="Léa").to_dict().keys() == {"id"}
