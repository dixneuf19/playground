from karaoke.reactions import BURST, EFFECT_COST, MAX_BATCH, RATE, RateLimit, Reactions


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


def test_effects_cost_more() -> None:
    r = Reactions()
    effects = 0
    while r.command("ABCD", "Léa", "confetti", 1, now=0) == {
        "action": "react",
        "effect": "confetti",
    }:
        effects += 1
    assert effects == BURST // EFFECT_COST
