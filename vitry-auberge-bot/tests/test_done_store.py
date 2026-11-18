import asyncio
import os
from datetime import date

import asyncpg
import pytest

from vitry_auberge_bot.done_store import DoneStore

DATABASE_URL = os.getenv("VITRY_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="VITRY_TEST_DATABASE_URL is not set")
def test_done_store_keeps_latest_day():
    async def scenario() -> None:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("DROP TABLE IF EXISTS chores_done")
        await conn.close()
        store = await DoneStore.connect(dsn=DATABASE_URL)
        try:
            assert await store.last_done() == {}
            await store.record("verre", date(2026, 10, 14), "Alice")
            await store.record("poubelle-marron", date(2026, 10, 9), "Bob")
            await store.record("poubelle-marron", date(2026, 10, 5), "Carol")
            assert await store.last_done() == {"verre": date(2026, 10, 14), "poubelle-marron": date(2026, 10, 9)}
            await store.record("verre", date(2026, 11, 11), "Bob")
            assert (await store.last_done())["verre"] == date(2026, 11, 11)
        finally:
            await store.close()

    asyncio.run(scenario())
