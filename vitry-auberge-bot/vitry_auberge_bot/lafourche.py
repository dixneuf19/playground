"""La Fourche deliveries: poll the orders, announce new ones, remind the evening before."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Protocol

import asyncpg
import httpx2

from vitry_auberge_bot.chores import TZ, format_day, format_hour

logger = logging.getLogger(__name__)

POLL_TIMES = (time(9, 0, tzinfo=TZ), time(18, 30, tzinfo=TZ))
REMINDER_TIME = time(19, 0, tzinfo=TZ)

# Public web config of lafourche.fr (Firebase project production-la-fourche)
FIREBASE_API_KEY = "AIzaSyDt_BPgkSXBnG_4VBmSXL04jMW03kj7whg"
SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
GRAPHQL_URL = "https://api.lafourche.fr/graphql"
ORDERS_QUERY = """
query GetCustomerOrder($pageSize: Int) {
  getCustomerOrder(pageSize: $pageSize) {
    items {
      id
      name
      cancelledAt
      updatedTimeSlot { deliveryDate startTime endTime }
    }
  }
}
"""


@dataclass(frozen=True)
class Order:
    id: str
    delivery_start: datetime
    delivery_end: datetime
    cancelled: bool = False

    def same_slot(self, other: Order) -> bool:
        return (self.delivery_start, self.delivery_end) == (other.delivery_start, other.delivery_end)


class OrderSource(Protocol):
    async def fetch_orders(self) -> list[Order]:
        """Orders of the account, with their delivery slot."""
        ...


class LaFourcheClient:
    """The GraphQL API behind lafourche.fr, authenticated with the account's Firebase email and password."""

    def __init__(self, email: str, password: str, page_size: int = 10) -> None:
        self.email = email
        self.password = password
        self.page_size = page_size

    async def fetch_orders(self) -> list[Order]:
        async with httpx2.AsyncClient(timeout=30) as http:
            sign_in = await http.post(
                SIGN_IN_URL,
                params={"key": FIREBASE_API_KEY},
                json={"email": self.email, "password": self.password, "returnSecureToken": True},
            )
            sign_in.raise_for_status()
            response = await http.post(
                GRAPHQL_URL,
                headers={"authorization": f"Bearer {sign_in.json()['idToken']}", "lf-channel": "default:fr_FR"},
                json={
                    "operationName": "GetCustomerOrder",
                    "query": ORDERS_QUERY,
                    "variables": {"pageSize": self.page_size},
                },
            )
            response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"La Fourche API errors: {payload['errors']}")
        return parse_orders(payload["data"]["getCustomerOrder"]["items"])


def _parse_time(day: date, value: str) -> datetime:
    """ "18:00", "18:00:00" or a full ISO datetime."""
    if "T" in value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)
    return datetime.combine(day, time.fromisoformat(value), tzinfo=TZ)


def parse_orders(items: list[dict[str, Any]]) -> list[Order]:
    orders = []
    for item in items:
        slot = item.get("updatedTimeSlot")
        if not slot or not slot.get("deliveryDate"):
            logger.info("Order %s has no delivery slot", item.get("name"))
            continue
        try:
            day = date.fromisoformat(slot["deliveryDate"][:10])
            start, end = _parse_time(day, slot["startTime"]), _parse_time(day, slot["endTime"])
        except KeyError, TypeError, ValueError:
            logger.warning("Unreadable delivery slot for order %s: %s", item.get("name"), slot)
            continue
        orders.append(Order(str(item["id"]), start, end, cancelled=item.get("cancelledAt") is not None))
    return orders


@dataclass(frozen=True)
class Changes:
    new: list[Order]
    # (previously known, now fetched)
    rescheduled: list[tuple[Order, Order]]
    cancelled: list[Order]


def compare(known: dict[str, Order], fetched: list[Order], now: datetime) -> Changes:
    """New, rescheduled and cancelled orders, ignoring deliveries already over.

    `known` holds the announced orders not cancelled yet.
    """
    new: list[Order] = []
    rescheduled: list[tuple[Order, Order]] = []
    cancelled: list[Order] = []
    for order in sorted(fetched, key=lambda o: o.delivery_start):
        if order.delivery_end <= now:
            continue
        previous = known.get(order.id)
        if order.cancelled:
            if previous is not None:
                cancelled.append(previous)
        elif previous is None:
            new.append(order)
        elif not previous.same_slot(order):
            rescheduled.append((previous, order))
    return Changes(new, rescheduled, cancelled)


def format_slot(order: Order) -> str:
    """e.g. "jeu. 13/11 entre 18h et 20h"."""
    start = order.delivery_start.astimezone(TZ)
    end = order.delivery_end.astimezone(TZ)
    return f"{format_day(start.date())} entre {format_hour(start)} et {format_hour(end)}"


def new_order_message(order: Order) -> str:
    return f"🧺 Nouvelle commande <b>La Fourche</b> ! Livraison le {format_slot(order)}."


def rescheduled_message(previous: Order, order: Order) -> str:
    return f"🔄 La livraison <b>La Fourche</b> a changé : le {format_slot(order)} (au lieu du {format_slot(previous)})."


def cancelled_message(order: Order) -> str:
    return f"❌ La commande <b>La Fourche</b> du {format_slot(order)} est annulée."


def reminder_message(order: Order) -> str:
    start = order.delivery_start.astimezone(TZ)
    end = order.delivery_end.astimezone(TZ)
    return (
        f"⚠️ Rappel : une commande <b>La Fourche</b> arrive demain ({format_day(start.date())}) "
        f"entre {format_hour(start)} et {format_hour(end)} !"
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS lafourche_orders (
    id             text PRIMARY KEY,
    delivery_start timestamptz NOT NULL,
    delivery_end   timestamptz NOT NULL,
    announced_at   timestamptz NOT NULL DEFAULT now(),
    reminded_at    timestamptz,
    cancelled_at   timestamptz
)
"""


def _order(row: asyncpg.Record) -> Order:
    return Order(row["id"], row["delivery_start"].astimezone(TZ), row["delivery_end"].astimezone(TZ))


class Store:
    """Orders already announced, and whether their reminder went out."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, **connect_kwargs: Any) -> Store:
        pool = await asyncpg.create_pool(min_size=1, max_size=2, **connect_kwargs)
        await pool.execute(SCHEMA)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def known(self, now: datetime) -> dict[str, Order]:
        rows = await self.pool.fetch(
            "SELECT id, delivery_start, delivery_end FROM lafourche_orders"
            " WHERE delivery_end > $1 AND cancelled_at IS NULL",
            now,
        )
        return {row["id"]: _order(row) for row in rows}

    async def add(self, order: Order) -> None:
        await self.pool.execute(
            "INSERT INTO lafourche_orders (id, delivery_start, delivery_end) VALUES ($1, $2, $3)"
            " ON CONFLICT (id) DO NOTHING",
            order.id,
            order.delivery_start,
            order.delivery_end,
        )

    async def reschedule(self, order: Order) -> None:
        """New slot: the reminder is due again for the new day."""
        await self.pool.execute(
            "UPDATE lafourche_orders SET delivery_start = $2, delivery_end = $3, reminded_at = NULL WHERE id = $1",
            order.id,
            order.delivery_start,
            order.delivery_end,
        )

    async def cancel(self, order: Order) -> None:
        await self.pool.execute("UPDATE lafourche_orders SET cancelled_at = now() WHERE id = $1", order.id)

    async def due_reminders(self, day: date) -> list[Order]:
        """Orders delivered on `day` (Paris time) whose reminder has not been sent."""
        rows = await self.pool.fetch(
            "SELECT id, delivery_start, delivery_end FROM lafourche_orders"
            " WHERE (delivery_start AT TIME ZONE 'Europe/Paris')::date = $1 AND reminded_at IS NULL AND cancelled_at IS NULL"
            " ORDER BY delivery_start",
            day,
        )
        return [_order(row) for row in rows]

    async def mark_reminded(self, order: Order) -> None:
        await self.pool.execute("UPDATE lafourche_orders SET reminded_at = now() WHERE id = $1", order.id)
