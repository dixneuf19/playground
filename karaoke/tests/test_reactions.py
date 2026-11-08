from karaoke.reactions import (
    BURST,
    EFFECT_COST,
    MAX_BATCH,
    RATE,
    ROOM_EFFECT_BURST,
    ROOM_EMOJI_BURST,
    ROOM_EMOJI_RATE,
    Kind,
    RateLimit,
    Reactions,
)


def test_rate_limit_grants_a_burst_then_refills() -> None:
    limit = RateLimit(rate=2, burst=5)
    assert limit.take("a", 3, now=0) == 3
    assert limit.take("a", 3, now=0) == 2  # only what's left
    assert limit.take("a", 1, now=0) == 0
    assert limit.take("b", 5, now=0) == 5  # per sender
    assert limit.take("a", 3, now=1) == 2  # 2 tokens a second
    assert limit.take("a", 10, now=100) == 5  # never more than the burst


def test_rate_limit_cost_per_unit() -> None:
    limit = RateLimit(rate=1, burst=10)
    assert limit.take("a", 1, now=0, cost=6) == 1
    assert limit.take("a", 1, now=0, cost=6) == 0  # 4 tokens left


def test_rate_limit_forgets_idle_senders() -> None:
    limit = RateLimit(rate=1, burst=2)
    for i in range(1001):
        limit.take(i, 1, now=0)
    limit.take("late", 1, now=10)  # everyone else is full again
    assert set(limit._buckets) == {"late"}


def test_emoji_are_counted_and_throttled_per_room_and_sender() -> None:
    r = Reactions()
    assert r.command("ABCD", "Léa", "fire", 3, now=0) == {
        "action": "react",
        "emoji": "🔥",
        "count": 3,
    }
    sent = 3
    while (cmd := r.command("ABCD", "Léa", "heart", MAX_BATCH, now=0)) is not None:
        count = cmd["count"]
        assert isinstance(count, int)
        sent += count
    assert sent == BURST
    assert r.command("ABCD", "Tom", "heart", 1, now=0) is not None
    assert r.command("WXYZ", "Léa", "heart", 1, now=0) is not None
    assert r.command("ABCD", "Léa", "heart", 1, now=1 / RATE) is not None


def _sent(r: Reactions, code: str, senders: range, kind: Kind) -> int:
    total = 0
    for s in senders:
        cmd = r.command(code, f"name-{s}", kind, MAX_BATCH, now=0)
        count = 0 if cmd is None else cmd.get("count", 1)
        assert isinstance(count, int)
        total += count
    return total


def test_rotating_names_cannot_exceed_the_room_cap() -> None:
    r = Reactions()
    assert _sent(r, "ABCD", range(100), "fire") == ROOM_EMOJI_BURST
    assert _sent(r, "ABCD", range(100), "confetti") == ROOM_EFFECT_BURST
    assert _sent(r, "WXYZ", range(100), "fire") == ROOM_EMOJI_BURST  # per room
    # and it refills at the room's rate
    assert r.command("ABCD", "new", "fire", MAX_BATCH, now=0.1) == {
        "action": "react",
        "emoji": "🔥",
        "count": int(0.1 * ROOM_EMOJI_RATE),
    }


def test_effects_cost_more_and_a_full_room_does_not_drain_its_senders() -> None:
    r = Reactions()
    assert BURST // EFFECT_COST > ROOM_EFFECT_BURST
    assert _sent(r, "ABCD", range(1), "hearts") == 1
    _sent(r, "ABCD", range(1, 100), "hearts")  # the room's effects are used up
    for _ in range(10):  # refused: costs Léa nothing
        assert r.command("ABCD", "Léa", "confetti", 1, now=0) is None
    assert r.sender.available(("ABCD", "Léa"), now=0) == BURST
