from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

from vitry_auberge_bot.chores import LastDone

SCHEMA = """
CREATE TABLE IF NOT EXISTS chores_done (
    chore_id text PRIMARY KEY,
    day      date NOT NULL,
    done_by  text NOT NULL,
    done_at  timestamptz NOT NULL DEFAULT now()
)
"""


class DoneStore:
    """Last day each chore was done, so its mute survives a restart."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, **connect_kwargs: Any) -> DoneStore:
        pool = await asyncpg.create_pool(min_size=1, max_size=2, **connect_kwargs)
        await pool.execute(SCHEMA)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def last_done(self) -> LastDone:
        rows = await self.pool.fetch("SELECT chore_id, day FROM chores_done")
        return {row["chore_id"]: row["day"] for row in rows}

    async def record(self, chore_id: str, day: date, done_by: str) -> None:
        await self.pool.execute(
            "INSERT INTO chores_done (chore_id, day, done_by) VALUES ($1, $2, $3)"
            " ON CONFLICT (chore_id) DO UPDATE SET day = $2, done_by = $3, done_at = now()"
            " WHERE chores_done.day <= $2",
            chore_id,
            day,
            done_by,
        )
