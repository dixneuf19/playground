"""Answers in Postgres: one row per (round, player, candidate).

A player is a random UUID kept in the browser's localStorage, nothing else
identifies them. Submitting again replaces the player's previous answers.
"""

import uuid
from dataclasses import dataclass

import asyncpg

SCHEMA = """
    CREATE TABLE IF NOT EXISTS answers (
        round_id   text NOT NULL,
        player     uuid NOT NULL,
        candidate  text NOT NULL,
        x          real NOT NULL CHECK (x BETWEEN -1 AND 1),
        y          real NOT NULL CHECK (y BETWEEN -1 AND 1),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (round_id, player, candidate)
    );
"""

# Above this many answers for a candidate, results only carry a sample of the
# points (the page draws them as a cloud, so a sample looks the same).
POINTS_MAX = 300


@dataclass(frozen=True)
class CandidateResult:
    candidate: str
    count: int
    median_x: float
    median_y: float
    points: list[tuple[float, float]]


class Store:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> "Store":
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
        async with pool.acquire() as conn:
            await conn.execute(SCHEMA)
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def save(
        self,
        round_id: str,
        player: uuid.UUID,
        positions: dict[str, tuple[float, float]],
    ) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM answers WHERE round_id = $1 AND player = $2",
                round_id,
                player,
            )
            await conn.executemany(
                "INSERT INTO answers (round_id, player, candidate, x, y) "
                "VALUES ($1, $2, $3, $4, $5)",
                [(round_id, player, c, x, y) for c, (x, y) in positions.items()],
            )

    async def players(self, round_id: str) -> int:
        return await self.pool.fetchval(
            "SELECT count(DISTINCT player) FROM answers WHERE round_id = $1",
            round_id,
        )

    async def results(self, round_id: str) -> list[CandidateResult]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT candidate, count(*) AS n,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY x) AS mx,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY y) AS my
                FROM answers WHERE round_id = $1 GROUP BY candidate
                """,
                round_id,
            )
            points = await conn.fetch(
                """
                SELECT candidate, x, y FROM (
                    SELECT candidate, x, y,
                           row_number() OVER (
                               PARTITION BY candidate ORDER BY random()
                           ) AS i
                    FROM answers WHERE round_id = $1
                ) sampled WHERE i <= $2
                """,
                round_id,
                POINTS_MAX,
            )
        by_candidate: dict[str, list[tuple[float, float]]] = {}
        for p in points:
            by_candidate.setdefault(p["candidate"], []).append(
                (round(p["x"], 3), round(p["y"], 3))
            )
        return [
            CandidateResult(
                candidate=r["candidate"],
                count=r["n"],
                median_x=round(r["mx"], 3),
                median_y=round(r["my"], 3),
                points=by_candidate.get(r["candidate"], []),
            )
            for r in rows
        ]
