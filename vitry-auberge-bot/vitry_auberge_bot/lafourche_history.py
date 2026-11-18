"""Copy every La Fourche order and its line items to Postgres, for price and spending stats.

Run as a one-shot command: `python -m vitry_auberge_bot.lafourche_history`.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
from dotenv import load_dotenv

from vitry_auberge_bot.chores import TZ
from vitry_auberge_bot.lafourche import LaFourcheClient, _slot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Line:
    id: str
    product_id: str | None
    variant_id: str | None
    sku: str | None
    title: str
    variant_title: str | None
    vendor: str | None
    quantity: int
    unit_price: Decimal
    grams: int | None
    total_discount: Decimal


@dataclass(frozen=True)
class Purchase:
    id: str
    name: str
    ordered_at: datetime
    delivery_date: date | None
    cancelled_at: datetime | None
    currency: str | None
    total_price: Decimal | None
    subtotal_price: Decimal | None
    total_discounts: Decimal | None
    shipping_price: Decimal | None
    lines: list[Line]
    payload: dict[str, Any]


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _id(value: Any) -> str | None:
    return None if value is None else str(value)


def parse_line(item: dict[str, Any]) -> Line:
    grams = item.get("grams")
    return Line(
        id=str(item["id"]),
        product_id=_id(item.get("productId")),
        variant_id=_id(item.get("variantId")),
        sku=item.get("sku") or None,
        title=item.get("title") or item.get("name") or "",
        variant_title=item.get("variantTitle") or None,
        vendor=item.get("vendor") or None,
        quantity=int(item["quantity"]),
        unit_price=_decimal(item["price"]) or Decimal(0),
        grams=int(grams) if grams else None,
        total_discount=_decimal(item.get("totalDiscount")) or Decimal(0),
    )


def parse_purchase(item: dict[str, Any]) -> Purchase:
    try:
        slot = _slot(item)
    except KeyError, TypeError, ValueError:
        slot = None
    shipping = ((item.get("totalShippingPriceSet") or {}).get("shopMoney") or {}).get("amount")
    ordered_at = _datetime(item.get("processedAt")) or _datetime(item.get("createdAt"))
    if ordered_at is None:
        raise ValueError(f"Order {item.get('name')} has no date")
    return Purchase(
        id=str(item["id"]),
        name=item.get("name") or "",
        ordered_at=ordered_at,
        delivery_date=slot[0].astimezone(TZ).date() if slot else None,
        cancelled_at=_datetime(item.get("cancelledAt")),
        currency=item.get("currency"),
        total_price=_decimal(item.get("totalPrice")),
        subtotal_price=_decimal(item.get("subtotalPrice")),
        total_discounts=_decimal(item.get("totalDiscounts")),
        shipping_price=_decimal(shipping),
        lines=[parse_line(line) for line in item.get("lineItems") or []],
        payload=item,
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS lafourche_purchases (
    id              text PRIMARY KEY,
    name            text NOT NULL,
    ordered_at      timestamptz NOT NULL,
    delivery_date   date,
    cancelled_at    timestamptz,
    currency        text,
    total_price     numeric(10, 2),
    subtotal_price  numeric(10, 2),
    total_discounts numeric(10, 2),
    shipping_price  numeric(10, 2),
    payload         jsonb NOT NULL,
    synced_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS lafourche_purchase_lines (
    purchase_id    text NOT NULL REFERENCES lafourche_purchases (id) ON DELETE CASCADE,
    id             text NOT NULL,
    product_id     text,
    variant_id     text,
    sku            text,
    title          text NOT NULL,
    variant_title  text,
    vendor         text,
    quantity       integer NOT NULL,
    unit_price     numeric(10, 2) NOT NULL,
    grams          integer,
    total_discount numeric(10, 2) NOT NULL DEFAULT 0,
    price_per_kg   numeric(10, 2) GENERATED ALWAYS AS (
        CASE WHEN grams > 0 THEN unit_price * 1000 / grams END
    ) STORED,
    PRIMARY KEY (purchase_id, id)
);
"""


class HistoryStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, **connect_kwargs: Any) -> HistoryStore:
        pool = await asyncpg.create_pool(min_size=1, max_size=2, **connect_kwargs)
        await pool.execute(SCHEMA)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def save(self, purchase: Purchase) -> None:
        """Insert or refresh an order. Its lines are replaced: items can be added until preparation."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO lafourche_purchases (id, name, ordered_at, delivery_date, cancelled_at, currency,
                    total_price, subtotal_price, total_discounts, shipping_price, payload)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name, ordered_at = EXCLUDED.ordered_at, delivery_date = EXCLUDED.delivery_date,
                    cancelled_at = EXCLUDED.cancelled_at, currency = EXCLUDED.currency,
                    total_price = EXCLUDED.total_price, subtotal_price = EXCLUDED.subtotal_price,
                    total_discounts = EXCLUDED.total_discounts, shipping_price = EXCLUDED.shipping_price,
                    payload = EXCLUDED.payload, synced_at = now()
                """,
                purchase.id,
                purchase.name,
                purchase.ordered_at,
                purchase.delivery_date,
                purchase.cancelled_at,
                purchase.currency,
                purchase.total_price,
                purchase.subtotal_price,
                purchase.total_discounts,
                purchase.shipping_price,
                json.dumps(purchase.payload),
            )
            await conn.execute("DELETE FROM lafourche_purchase_lines WHERE purchase_id = $1", purchase.id)
            await conn.executemany(
                """
                INSERT INTO lafourche_purchase_lines (purchase_id, id, product_id, variant_id, sku, title,
                    variant_title, vendor, quantity, unit_price, grams, total_discount)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                [
                    (
                        purchase.id,
                        line.id,
                        line.product_id,
                        line.variant_id,
                        line.sku,
                        line.title,
                        line.variant_title,
                        line.vendor,
                        line.quantity,
                        line.unit_price,
                        line.grams,
                        line.total_discount,
                    )
                    for line in purchase.lines
                ],
            )


async def sync(client: LaFourcheClient, store: HistoryStore) -> int:
    items = await client.fetch_order_history()
    saved = 0
    for item in items:
        try:
            purchase = parse_purchase(item)
        except KeyError, TypeError, ValueError:
            logger.exception("Skipping unreadable order %s", item.get("name"))
            continue
        await store.save(purchase)
        saved += 1
    return saved


async def run() -> None:
    email = os.getenv("LAFOURCHE_EMAIL")
    if not email:
        raise RuntimeError("LAFOURCHE_EMAIL environment variable is required")
    client = LaFourcheClient(email, os.getenv("LAFOURCHE_PASSWORD", ""))
    store = await HistoryStore.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT") or 5432),
        database=os.getenv("DB_NAME", "vitry_auberge_bot"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    try:
        saved = await sync(client, store)
    finally:
        await store.close()
    logger.info("Synced %d La Fourche orders", saved)


def main() -> None:
    load_dotenv()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
