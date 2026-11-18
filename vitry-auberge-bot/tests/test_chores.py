from datetime import date, datetime

from vitry_auberge_bot.chores import CHORES_BY_ID, TZ, format_when, in_progress, upcoming

MONDAY = date(2026, 10, 5)
WEDNESDAY_ODD_WEEK = date(2026, 10, 7)
WEDNESDAY_EVEN_WEEK = date(2026, 10, 14)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def test_brown_bin_on_monday_and_friday():
    chore = CHORES_BY_ID["poubelle-marron"]
    assert [chore.occurs_on(date(2026, 10, d)) for d in range(5, 12)] == [True, False, False, False, True, False, False]


def test_yellow_bin_reminded_once_at_22h():
    assert CHORES_BY_ID["poubelle-jaune"].reminder_times(WEDNESDAY_ODD_WEEK) == [at(WEDNESDAY_ODD_WEEK, 22)]


def test_brown_bin_and_glass_have_no_reminder():
    assert CHORES_BY_ID["poubelle-marron"].reminders == ()
    assert CHORES_BY_ID["verre"].reminders == ()


def test_glass_only_on_even_weeks():
    glass = CHORES_BY_ID["verre"]
    assert not glass.occurs_on(WEDNESDAY_ODD_WEEK)
    assert glass.occurs_on(WEDNESDAY_EVEN_WEEK)


def test_vegetables_reminders():
    chore = CHORES_BY_ID["legumes"]
    assert chore.start_at(MONDAY) == at(MONDAY, 17)
    assert chore.reminder_times(MONDAY) == [at(MONDAY, 20)]


def test_cleaning_has_no_reminder():
    assert CHORES_BY_ID["menage"].reminders == ()


def test_reminders_keep_wall_clock_across_dst():
    # Last Sunday of October 2026 is the 25th: Wednesday the 28th is in winter time
    day = date(2026, 10, 28)
    assert [t.hour for t in CHORES_BY_ID["poubelle-jaune"].reminder_times(day)] == [22]


def test_upcoming_lists_week_in_order():
    chores = [chore.id for _, chore in upcoming(at(MONDAY, 12), {})]
    assert chores == [
        "legumes",
        "poubelle-marron",
        "poubelle-jaune",
        "poubelle-marron",
        "menage",
    ]


def test_in_progress_until_last_reminder():
    assert [(d, c.id) for d, c in in_progress(at(WEDNESDAY_ODD_WEEK, 21))] == [(WEDNESDAY_ODD_WEEK, "poubelle-jaune")]
    assert in_progress(at(WEDNESDAY_ODD_WEEK, 22, 30)) == []


def test_brown_bin_is_never_in_progress():
    assert in_progress(at(MONDAY, 20, 30)) == []


def test_only_brown_bin_and_glass_can_be_skipped():
    assert {chore_id for chore_id, chore in CHORES_BY_ID.items() if chore.skippable} == {"poubelle-marron", "verre"}


def test_brown_bin_muted_for_the_next_two_pickups():
    brown = CHORES_BY_ID["poubelle-marron"]
    # Done on a Monday: Friday and the next Monday are muted, the Friday after rings
    assert [
        d for d in range(6, 20) if brown.occurs_on(date(2026, 10, d)) and not brown.muted_on(date(2026, 10, d), MONDAY)
    ] == [16, 19]
    # Done on a Friday: Monday and Friday are muted, the Monday after rings
    friday = date(2026, 10, 9)
    assert [brown.muted_on(date(2026, 10, d), friday) for d in (12, 16, 19)] == [True, True, False]


def test_glass_muted_for_one_month():
    glass = CHORES_BY_ID["verre"]
    assert glass.muted_on(date(2026, 10, 28), WEDNESDAY_EVEN_WEEK)
    assert not glass.muted_on(date(2026, 11, 11), WEDNESDAY_EVEN_WEEK)


def test_not_muted_without_done_or_for_other_chores():
    assert not CHORES_BY_ID["poubelle-marron"].muted_on(date(2026, 10, 9), None)
    assert not CHORES_BY_ID["poubelle-jaune"].muted_on(date(2026, 10, 14), WEDNESDAY_ODD_WEEK)


def test_upcoming_hides_muted_chores():
    chores = [chore.id for _, chore in upcoming(at(MONDAY, 21), {"poubelle-marron": MONDAY})]
    assert chores == ["poubelle-jaune", "menage", "legumes"]


def test_format_when_in_french():
    assert format_when(at(MONDAY, 20)) == "lun. 05/10 20h"
    assert format_when(at(date(2026, 10, 11), 18, 30)) == "dim. 11/10 18h30"
