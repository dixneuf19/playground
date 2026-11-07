"""Leader election: one replica runs the background work, the others only serve.

The leader holds a session-level Postgres advisory lock on a connection of its
own. If the pod stops or dies, its connection closes and Postgres releases the
lock; another replica takes it at its next attempt. TCP keepalives make Postgres
notice a vanished node within about half a minute.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

import asyncpg

logger = logging.getLogger(__name__)

LEADER_LOCK = 4242_0003  # pg advisory lock key
POLL_SECONDS = 5
PROBE_TIMEOUT = 5
# Postgres-side keepalives on the lock connection: idle 10 s, then 3 probes 5 s apart.
KEEPALIVES = {
    "tcp_keepalives_idle": "10",
    "tcp_keepalives_interval": "5",
    "tcp_keepalives_count": "3",
}


class Leadership:
    def __init__(
        self,
        dsn: str,
        on_gain: Callable[[], Awaitable[None]],
        on_lose: Callable[[], Awaitable[None]],
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.dsn = dsn
        self.on_gain = on_gain
        self.on_lose = on_lose
        self.poll_seconds = poll_seconds
        self.is_leader = False
        self._conn: asyncpg.Connection | None = None

    async def step(self) -> None:
        """Try to become leader, or check that we still are."""
        try:
            if self.is_leader:
                await self._check()
            else:
                await self._try()
        except Exception:
            logger.exception("Leader election step failed")
            await self._step_down()

    async def run(self) -> None:
        """Forever, after a first `step()` done at startup."""
        while True:
            await asyncio.sleep(self.poll_seconds)
            await self.step()

    async def _try(self) -> None:
        if self._conn is None or self._conn.is_closed():
            self._conn = await asyncpg.connect(self.dsn, server_settings=KEEPALIVES)
        if await self._conn.fetchval("SELECT pg_try_advisory_lock($1)", LEADER_LOCK):
            self.is_leader = True
            logger.info("This replica is now the leader")
            await self.on_gain()

    async def _check(self) -> None:
        assert self._conn is not None
        # The lock lives as long as this connection: if it answers, we lead.
        await asyncio.wait_for(self._conn.fetchval("SELECT 1"), PROBE_TIMEOUT)

    async def _step_down(self) -> None:
        if self.is_leader:
            self.is_leader = False
            logger.warning("Lost leadership")
            await self.on_lose()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None

    async def close(self) -> None:
        """Release leadership right away (closing the connection drops the lock)."""
        await self._step_down()
