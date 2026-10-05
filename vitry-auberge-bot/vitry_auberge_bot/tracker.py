from dataclasses import dataclass, field
from datetime import date, timedelta

from vitry_auberge_bot.chores import Chore

type OccurrenceKey = tuple[str, date]


@dataclass
class Occurrence:
    chore: Chore
    day: date
    message_ids: list[int] = field(default_factory=list)
    done_by: str | None = None
    skipped: bool = False

    @property
    def key(self) -> OccurrenceKey:
        return (self.chore.id, self.day)

    @property
    def done(self) -> bool:
        return self.done_by is not None


class Tracker:
    """In-memory state of the reminders sent and who acknowledged them."""

    def __init__(self) -> None:
        self._occurrences: dict[OccurrenceKey, Occurrence] = {}
        self._by_message: dict[int, Occurrence] = {}

    def get(self, chore: Chore, day: date) -> Occurrence:
        key = (chore.id, day)
        if key not in self._occurrences:
            self._forget_before(day - timedelta(days=2))
            self._occurrences[key] = Occurrence(chore, day)
        return self._occurrences[key]

    def find(self, key: OccurrenceKey) -> Occurrence | None:
        return self._occurrences.get(key)

    def add_message(self, occurrence: Occurrence, message_id: int) -> None:
        occurrence.message_ids.append(message_id)
        self._by_message[message_id] = occurrence

    def by_message(self, message_id: int) -> Occurrence | None:
        return self._by_message.get(message_id)

    def acknowledge(self, occurrence: Occurrence, who: str, skipped: bool = False) -> bool:
        """Mark as done, or as not needed this time. Returns False if someone already handled it."""
        if occurrence.done:
            return False
        occurrence.done_by = who
        occurrence.skipped = skipped
        return True

    def _forget_before(self, day: date) -> None:
        for key, occurrence in list(self._occurrences.items()):
            if occurrence.day < day:
                del self._occurrences[key]
                for message_id in occurrence.message_ids:
                    self._by_message.pop(message_id, None)
