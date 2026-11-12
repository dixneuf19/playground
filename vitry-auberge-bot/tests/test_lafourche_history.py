import asyncio
import os
from datetime import UTC, date, datetime
from decimal import Decimal

import asyncpg
import pytest

from vitry_auberge_bot.lafourche_history import HistoryStore, parse_purchase

ITEM = {
    "id": "gid://shopify/Order/1",
    "name": "#1001",
    "createdAt": "2026-11-10T08:00:00Z",
    "processedAt": "2026-11-10T08:00:05Z",
    "cancelledAt": None,
    "currency": "EUR",
    "totalPrice": "42.50",
    "subtotalPrice": "39.00",
    "totalDiscounts": "1.00",
    "totalShippingPriceSet": {"shopMoney": {"amount": "3.50", "currencyCode": "EUR"}},
    "noteAttributes": [
        {"name": "delivery_date", "value": "2026/11/13"},
        {"name": "delivery_time_slot", "value": "18:00 - 20:00"},
    ],
    "updatedTimeSlot": None,
    "lineItems": [
        {
            "id": 11,
            "productId": 21,
            "variantId": 31,
            "sku": "LF-PATES",
            "title": "Penne bio",
            "variantTitle": "500 g",
            "vendor": "Alce Nero",
            "quantity": 2,
            "price": "1.80",
            "grams": 500,
            "totalDiscount": "0.00",
        },
        {"id": 12, "title": "Sac consigné", "quantity": 1, "price": 0.5, "grams": 0, "totalDiscount": None},
    ],
}


def test_parse_purchase():
    purchase = parse_purchase(ITEM)
    assert purchase.ordered_at == datetime(2026, 11, 10, 8, 0, 5, tzinfo=UTC)
    assert purchase.delivery_date == date(2026, 11, 13)
    assert purchase.total_price == Decimal("42.50")
    assert purchase.shipping_price == Decimal("3.50")
    penne, bag = purchase.lines
    assert (penne.id, penne.product_id, penne.quantity, penne.unit_price, penne.grams) == (
        "11",
        "21",
        2,
        Decimal("1.80"),
        500,
    )
    assert (bag.product_id, bag.unit_price, bag.grams, bag.total_discount) == (None, Decimal("0.5"), None, 0)


def test_order_without_slot_has_no_delivery_date():
    assert parse_purchase({**ITEM, "noteAttributes": []}).delivery_date is None


DATABASE_URL = os.getenv("VITRY_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="VITRY_TEST_DATABASE_URL is not set")
def test_history_store_round_trip():
    async def scenario() -> None:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("DROP TABLE IF EXISTS lafourche_purchase_lines, lafourche_purchases")
        await conn.close()
        store = await HistoryStore.connect(dsn=DATABASE_URL)
        try:
            await store.save(parse_purchase(ITEM))
            # Saving again replaces the lines: an item was removed before preparation
            await store.save(parse_purchase({**ITEM, "lineItems": ITEM["lineItems"][:1]}))
            rows = await store.pool.fetch(
                "SELECT title, price_per_kg FROM lafourche_purchase_lines WHERE purchase_id = $1", ITEM["id"]
            )
            assert [(r["title"], r["price_per_kg"]) for r in rows] == [("Penne bio", Decimal("3.60"))]
            assert await store.pool.fetchval("SELECT payload->>'name' FROM lafourche_purchases") == "#1001"
        finally:
            await store.close()

    asyncio.run(scenario())
