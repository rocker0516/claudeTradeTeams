"""Real-time Clock backed by datetime / time / asyncio."""

import asyncio
import time
from datetime import datetime, timezone

from .base import Clock


class RealClock(Clock):
    """Wall-clock + asyncio sleep implementation of Clock.

    Use in live and paper modes. Use SimulatedClock (TBD) in backtest.
    """

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
