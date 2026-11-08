"""Emoji and effects sent from phones to fly across the big screen.

Ephemeral: never stored, sent to the screens as a display command. Each sender
is throttled on the replica that receives it, and the screen caps how many
particles it animates, so spamming fills the TV without freezing it.
"""

from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Literal, get_args

# Order = the phone's buttons. Effects come last.
Kind = Literal[
    "heart",
    "laugh",
    "fire",
    "clap",
    "love",
    "rock",
    "cry",
    "praise",
    "confetti",
    "hearts",
]
KINDS: tuple[Kind, ...] = get_args(Kind)
EMOJI: dict[Kind, str] = {
    "heart": "❤️",
    "laugh": "😂",
    "fire": "🔥",
    "clap": "👏",
    "love": "😍",
    "rock": "🤘",
    "cry": "😭",
    "praise": "🙌",
    # effects: their button, the screen draws the effect
    "confetti": "🎉",
    "hearts": "💖",
}
EFFECTS: frozenset[Kind] = frozenset({"confetti", "hearts"})
MAX_BATCH = 10  # the phone groups quick taps into one message
EFFECT_COST = 5  # an effect draws many particles: it costs more tokens
RATE, BURST = 8, 30  # per sender and replica: tokens a second, bucket size
# Per room and replica, whoever sends: a script rotating names or sockets
# still can't flood the NOTIFYs. Well above what a real crowd taps.
ROOM_EMOJI_RATE, ROOM_EMOJI_BURST = 40, 80
ROOM_EFFECT_RATE, ROOM_EFFECT_BURST = 2, 4


@dataclass
class RateLimit:
    """Token buckets by key: `rate` tokens a second, up to `burst`."""

    rate: float
    burst: float
    _buckets: dict[Hashable, tuple[float, float]] = field(default_factory=dict)

    def _tokens(self, key: Hashable, now: float) -> float:
        tokens, at = self._buckets.get(key, (self.burst, now))
        return min(self.burst, tokens + (now - at) * self.rate)

    def available(self, key: Hashable, now: float, cost: float = 1) -> int:
        """How many units of `cost` tokens the bucket holds now."""
        return int(self._tokens(key, now) // cost)

    def spend(self, key: Hashable, units: int, now: float, cost: float = 1) -> None:
        self._buckets[key] = (self._tokens(key, now) - units * cost, now)
        if len(self._buckets) > 1000:
            self._prune(now)

    def take(self, key: Hashable, wanted: int, now: float, cost: float = 1) -> int:
        """How many of the `wanted` units (of `cost` tokens each) may go."""
        granted = max(0, min(wanted, self.available(key, now, cost)))
        self.spend(key, granted, now, cost)
        return granted

    def _prune(self, now: float) -> None:
        """Forget keys whose bucket is full again: same as never seen."""
        full = self.burst / self.rate
        self._buckets = {
            k: (t, at) for k, (t, at) in self._buckets.items() if now - at < full
        }


class Reactions:
    """Throttles each sender and each room, then says what to send to the
    screens. A reaction goes only as far as both buckets allow, and spends
    from both only then: a saturated room doesn't drain its senders."""

    def __init__(self) -> None:
        self.sender = RateLimit(RATE, BURST)
        self.room_emoji = RateLimit(ROOM_EMOJI_RATE, ROOM_EMOJI_BURST)
        self.room_effects = RateLimit(ROOM_EFFECT_RATE, ROOM_EFFECT_BURST)

    def command(
        self, code: str, sender: str, kind: Kind, count: int, now: float
    ) -> dict[str, object] | None:
        """An effect, or up to `count` of one emoji; None when throttled."""
        key = (code, sender)
        effect = kind in EFFECTS
        room = self.room_effects if effect else self.room_emoji
        cost = EFFECT_COST if effect else 1
        wanted = 1 if effect else min(count, MAX_BATCH)
        granted = min(
            wanted, self.sender.available(key, now, cost), room.available(code, now)
        )
        if granted <= 0:
            return None
        self.sender.spend(key, granted, now, cost)
        room.spend(code, granted, now)
        if effect:
            return {"action": "react", "effect": kind}
        return {"action": "react", "emoji": EMOJI[kind], "count": granted}
