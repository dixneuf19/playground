from datetime import date, datetime

from vitry_auberge_bot.chores import CHORES_BY_ID, TZ, in_progress, upcoming

MONDAY = date(2026, 10, 5)
WEDNESDAY_ODD_WEEK = date(2026, 10, 7)
WEDNESDAY_EVEN_WEEK = date(2026, 10, 14)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def test_brown_bin_on_monday_and_friday():
    chore = CHORES_BY_ID["poubelle-marron"]
    assert [chore.occurs_on(date(2026, 10, d)) for d in range(5, 12)] == [True, False, False, False, True, False, False]


def test_bin_reminders_every_two_hours_until_midnight():
    times = CHORES_BY_ID["poubelle-marron"].reminder_times(MONDAY)
    assert times == [at(MONDAY, 22), at(date(2026, 10, 6), 0)]


def test_glass_only_on_even_weeks():
    glass = CHORES_BY_ID["verre"]
    assert not glass.occurs_on(WEDNESDAY_ODD_WEEK)
    assert glass.occurs_on(WEDNESDAY_EVEN_WEEK)


def test_vegetables_reminders():
    chore = CHORES_BY_ID["legumes"]
    assert chore.start_at(MONDAY) == at(MONDAY, 17)
    assert chore.reminder_times(MONDAY) == [at(MONDAY, 19), at(MONDAY, 21)]


def test_cleaning_has_no_reminder():
    assert CHORES_BY_ID["menage"].reminders == ()


def test_reminders_keep_wall_clock_across_dst():
    # Last Sunday of October 2026 is the 25th: Monday the 26th is the first day of winter time
    day = date(2026, 10, 26)
    assert [t.hour for t in CHORES_BY_ID["poubelle-marron"].reminder_times(day)] == [22, 0]


def test_upcoming_lists_week_in_order():
    chores = [chore.id for _, chore in upcoming(at(MONDAY, 12))]
    assert chores == [
        "legumes",
        "poubelle-marron",
        "poubelle-jaune",
        "poubelle-marron",
        "menage",
    ]


def test_in_progress_after_midnight_belongs_to_previous_day():
    assert [(d, c.id) for d, c in in_progress(at(date(2026, 10, 5), 23))] == [(MONDAY, "poubelle-marron")]
    assert in_progress(at(date(2026, 10, 6), 0, 30)) == []


def test_in_progress_vegetables_and_bin_overlap():
    assert {c.id for _, c in in_progress(at(MONDAY, 20, 30))} == {"legumes", "poubelle-marron"}


def test_only_brown_bin_and_glass_can_be_skipped():
    assert {chore_id for chore_id, chore in CHORES_BY_ID.items() if chore.skippable} == {"poubelle-marron", "verre"}
