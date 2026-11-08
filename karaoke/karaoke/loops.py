import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def every(seconds: float, job: Callable[[], Awaitable[None]]) -> None:
    """Run a job forever, `seconds` apart; a failure is logged, not fatal."""
    while True:
        try:
            await job()
        except Exception:
            logger.exception("Background job %s failed", getattr(job, "__name__", job))
        await asyncio.sleep(seconds)
