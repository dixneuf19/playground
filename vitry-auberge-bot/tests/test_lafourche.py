import asyncio
import os
from datetime import date, datetime

import asyncpg
import pytest

from vitry_auberge_bot.chores import TZ
from vitry_auberge_bot.lafourche import (
    Order,
    Store,
    cancelled_message,
    compare,
    format_slot,
    new_order_message,
    parse_orders,
    reminder_message,
    rescheduled_message,
)

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=TZ)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def order(id: str, day: int, start: int, end: int) -> Order:
    return Order(id, at(day, start), at(day, end))


def test_unknown_orders_are_new_in_delivery_order():
    later, sooner = order("b", 14, 18, 20), order("a", 13, 10, 12)
    changes = compare({}, [later, sooner], NOW)
    assert changes.new == [sooner, later]
    assert changes.rescheduled == []


def test_known_order_with_same_slot_is_ignored():
    known = order("a", 13, 18, 20)
    assert compare({"a": known}, [order("a", 13, 18, 20)], NOW) == compare({}, [], NOW)


def test_known_order_with_new_slot_is_rescheduled():
    before, after = order("a", 13, 18, 20), order("a", 14, 8, 10)
    changes = compare({"a": before}, [after], NOW)
    assert changes.new == []
    assert changes.rescheduled == [(before, after)]


def test_only_deliveries_not_over_are_announced():
    ongoing = order("ongoing", 11, 8, 10)
    fetched = [order("old", 10, 18, 20), order("just-over", 11, 7, 9), ongoing]
    assert compare({}, fetched, NOW).new == [ongoing]


def test_cancelled_known_order_is_reported_once():
    known = order("a", 13, 18, 20)
    cancelled = Order("a", known.delivery_start, known.delivery_end, cancelled=True)
    assert compare({"a": known}, [cancelled], NOW).cancelled == [known]
    # Once recorded as cancelled the store no longer lists it as known
    assert compare({}, [cancelled], NOW) == compare({}, [], NOW)


def notes(day: str, slot: str) -> list[dict[str, str]]:
    return [
        {"name": "userId", "value": "x"},
        {"name": "delivery_date", "value": day},
        {"name": "delivery_time_slot", "value": slot},
    ]


def test_parse_orders_reads_the_checkout_slot_from_note_attributes():
    # As returned by api.lafourche.fr on 2026-11-12
    items = [
        {
            "id": "1",
            "name": "003741041",
            "cancelledAt": None,
            "updatedTimeSlot": None,
            "noteAttributes": notes("2026/11/16", "09:00 - 11:00"),
        },
        {
            "id": "2",
            "name": "#2",
            "cancelledAt": "2026-11-11T10:00:00Z",
            "updatedTimeSlot": None,
            "noteAttributes": notes("2026/11/14", "18:00 - 20:30"),
        },
        {"id": "3", "name": "#3", "cancelledAt": None, "updatedTimeSlot": None, "noteAttributes": []},
        {
            "id": "4",
            "name": "#4",
            "cancelledAt": None,
            "updatedTimeSlot": None,
            "noteAttributes": notes("demain", "matin"),
        },
    ]
    assert parse_orders(items) == [
        Order("1", at(16, 9), at(16, 11)),
        Order("2", at(14, 18), at(14, 20, 30), cancelled=True),
    ]


def test_updated_time_slot_wins_over_the_checkout_slot():
    item = {
        "id": "1",
        "name": "#1",
        "cancelledAt": None,
        "noteAttributes": notes("2026/11/16", "09:00 - 11:00"),
        "updatedTimeSlot": {"deliveryDate": "2026-11-17T00:00:00.000Z", "startTime": "18:00", "endTime": "20:00:00"},
    }
    assert parse_orders([item]) == [Order("1", at(17, 18), at(17, 20))]


def test_messages_in_french():
    o = Order("a", at(13, 18), at(13, 20, 30))
    assert format_slot(o) == "ven. 13/11 entre 18h et 20h30"
    assert new_order_message(o) == (
        "🧺 Nouvelle commande <b>La Fourche</b> ! Livraison le ven. 13/11 entre 18h et 20h30.\n"
        "Vous pouvez encore y ajouter des produits tant qu'elle n'est pas en préparation."
    )
    assert rescheduled_message(order("a", 12, 8, 10), o).endswith(
        "le ven. 13/11 entre 18h et 20h30 (au lieu du jeu. 12/11 entre 8h et 10h)."
    )
    assert cancelled_message(o) == "❌ La commande <b>La Fourche</b> du ven. 13/11 entre 18h et 20h30 est annulée."
    assert reminder_message(o) == (
        "⚠️ Rappel : une commande <b>La Fourche</b> arrive demain (ven. 13/11) entre 18h et 20h30 !"
    )


DATABASE_URL = os.getenv("VITRY_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="VITRY_TEST_DATABASE_URL is not set")
def test_store_round_trip():
    async def scenario() -> None:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("DROP TABLE IF EXISTS lafourche_orders")
        await conn.close()
        store = await Store.connect(dsn=DATABASE_URL)
        try:
            tomorrow = order("a", 12, 18, 20)
            await store.add(tomorrow)
            await store.add(order("past", 10, 18, 20))
            # Late in the evening Paris time is still the same day, not the next UTC one
            await store.add(Order("late", at(12, 23), at(13, 0, 30)))
            assert await store.known(NOW) == {"a": tomorrow, "late": Order("late", at(12, 23), at(13, 0, 30))}

            assert [o.id for o in await store.due_reminders(date(2026, 11, 12))] == ["a", "late"]
            await store.mark_reminded(tomorrow)
            assert [o.id for o in await store.due_reminders(date(2026, 11, 12))] == ["late"]

            moved = order("a", 14, 8, 10)
            await store.reschedule(moved)
            assert (await store.known(NOW))["a"] == moved
            assert [o.id for o in await store.due_reminders(date(2026, 11, 14))] == ["a"]

            await store.cancel(moved)
            assert "a" not in await store.known(NOW)
            assert await store.due_reminders(date(2026, 11, 14)) == []
        finally:
            await store.close()

    asyncio.run(scenario())
