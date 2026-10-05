from datetime import date

from vitry_auberge_bot.chores import CHORES_BY_ID
from vitry_auberge_bot.tracker import Tracker

BIN = CHORES_BY_ID["poubelle-marron"]


def test_any_message_of_an_occurrence_finds_it():
    tracker = Tracker()
    occurrence = tracker.get(BIN, date(2026, 10, 5))
    tracker.add_message(occurrence, 1)
    tracker.add_message(occurrence, 2)
    assert tracker.by_message(2) is occurrence
    assert tracker.by_message(3) is None


def test_only_first_acknowledgement_counts():
    tracker = Tracker()
    occurrence = tracker.get(BIN, date(2026, 10, 5))
    assert tracker.acknowledge(occurrence, "Alice")
    assert not tracker.acknowledge(occurrence, "Bob")
    assert occurrence.done_by == "Alice"


def test_old_occurrences_are_forgotten():
    tracker = Tracker()
    old = tracker.get(BIN, date(2026, 10, 1))
    tracker.add_message(old, 1)
    tracker.get(BIN, date(2026, 10, 5))
    assert tracker.find(old.key) is None
    assert tracker.by_message(1) is None
