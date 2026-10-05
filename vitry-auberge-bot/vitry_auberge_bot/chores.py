from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
DAY_NAMES = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


@dataclass(frozen=True)
class Chore:
    id: str
    label: str
    message: str
    weekdays: frozenset[int]
    start: time
    # Offsets from `start` at which to nag again while nobody has acknowledged
    reminders: tuple[timedelta, ...] = ()
    even_weeks_only: bool = False
    # Offers a "Pas besoin" button, for bins that are not always worth taking out
    skippable: bool = False

    def occurs_on(self, day: date) -> bool:
        if day.weekday() not in self.weekdays:
            return False
        return not (self.even_weeks_only and day.isocalendar().week % 2)

    def start_at(self, day: date) -> datetime:
        return datetime.combine(day, self.start, tzinfo=TZ)

    def reminder_times(self, day: date) -> list[datetime]:
        return [self.start_at(day) + offset for offset in self.reminders]

    def end_at(self, day: date) -> datetime:
        return max([self.start_at(day), *self.reminder_times(day)])


def every(hours: int, until: int) -> tuple[timedelta, ...]:
    """Reminder offsets every `hours` hours, up to `until` hours after the start."""
    return tuple(timedelta(hours=h) for h in range(hours, until + 1, hours))


EVENING = time(20, 0, tzinfo=TZ)

CHORES: tuple[Chore, ...] = (
    Chore(
        id="poubelle-marron",
        label="🟤 Poubelle marron",
        message="🟤 Ce soir on sort la <b>poubelle marron</b> (ordures ménagères) !",
        weekdays=frozenset({MON, FRI}),
        start=EVENING,
        reminders=every(2, until=4),
        skippable=True,
    ),
    Chore(
        id="poubelle-jaune",
        label="🟡 Poubelle jaune",
        message="🟡 Ce soir on sort la <b>poubelle jaune</b> (recyclables) !",
        weekdays=frozenset({WED}),
        start=EVENING,
        reminders=every(2, until=4),
    ),
    Chore(
        id="verre",
        label="🟢 Verre",
        message="🟢 Ce soir on sort le <b>verre</b> !",
        weekdays=frozenset({WED}),
        start=EVENING,
        reminders=every(2, until=4),
        even_weeks_only=True,
        skippable=True,
    ),
    Chore(
        id="legumes",
        label="🥕 Légumes du Rungis",
        message="🥕 Ce soir c'est la <b>récup des légumes du Rungis</b> à Bizet, entre 18h et 22h. Qui y va ?",
        weekdays=frozenset({MON}),
        start=time(17, 0, tzinfo=TZ),
        reminders=(timedelta(hours=2), timedelta(hours=4)),
    ),
    Chore(
        id="menage",
        label="🧹 Ménage hebdo",
        message="🧹 Est-ce que quelqu'un a fait le <b>ménage hebdo</b> ?",
        weekdays=frozenset({SUN}),
        start=time(19, 0, tzinfo=TZ),
    ),
)

CHORES_BY_ID = {chore.id: chore for chore in CHORES}


def upcoming(now: datetime, days: int = 7) -> list[tuple[datetime, Chore]]:
    """Chore occurrences starting between `now` and `days` days later, sorted."""
    result = []
    for offset in range(days + 1):
        day = now.date() + timedelta(days=offset)
        for chore in CHORES:
            if chore.occurs_on(day) and now <= chore.start_at(day) <= now + timedelta(days=days):
                result.append((chore.start_at(day), chore))
    return sorted(result, key=lambda item: item[0])


def in_progress(now: datetime) -> list[tuple[date, Chore]]:
    """Occurrences already started but with reminders still pending (used to resume after a restart)."""
    result = []
    for day in (now.date() - timedelta(days=1), now.date()):
        for chore in CHORES:
            if chore.occurs_on(day) and chore.start_at(day) < now < chore.end_at(day):
                result.append((day, chore))
    return result


def format_when(when: datetime) -> str:
    """French short date, e.g. "lun. 05/10 20h" or "dim. 11/10 18h30"."""
    hour = f"{when.hour}h{when.minute:02d}" if when.minute else f"{when.hour}h"
    return f"{DAY_NAMES[when.weekday()]} {when:%d/%m} {hour}"
