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


@dataclass
class RateLimit:
    """Token bucket per sender: `rate` tokens a second, up to `burst`."""

    rate: float
    burst: float
    _buckets: dict[Hashable, tuple[float, float]] = field(default_factory=dict)

    def take(self, key: Hashable, wanted: int, now: float, cost: float = 1) -> int:
        """How many of the `wanted` units (of `cost` tokens each) may go."""
        tokens, at = self._buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - at) * self.rate)
        granted = max(0, min(wanted, int(tokens // cost)))
        self._buckets[key] = (tokens - granted * cost, now)
        if len(self._buckets) > 1000:
            self._prune(now)
        return granted

    def _prune(self, now: float) -> None:
        """Forget senders whose bucket is full again: same as never seen."""
        full = self.burst / self.rate
        self._buckets = {
            k: (t, at) for k, (t, at) in self._buckets.items() if now - at < full
        }


class Reactions:
    """Throttles each sender, then says what to send to the screens."""

    def __init__(self) -> None:
        self.limit = RateLimit(RATE, BURST)

    def command(
        self, code: str, sender: str, kind: Kind, count: int, now: float
    ) -> dict[str, object] | None:
        """An effect, or up to `count` of one emoji; None when throttled."""
        key = (code, sender)
        if kind in EFFECTS:
            if not self.limit.take(key, 1, now, EFFECT_COST):
                return None
            return {"action": "react", "effect": kind}
        granted = self.limit.take(key, min(count, MAX_BATCH), now)
        if not granted:
            return None
        return {"action": "react", "emoji": EMOJI[kind], "count": granted}
