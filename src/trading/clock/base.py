"""Abstract clock.

Every time-related call in the system must go through this interface.
Direct use of `datetime.now()` or `time.time()` is forbidden by lint —
backtest mode runs on a simulated clock that advances on data events,
not wall time, so any escape would silently break reproducibility.
"""

from abc import ABC, abstractmethod
from datetime import datetime


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Current time as timezone-aware datetime (UTC)."""
        ...

    @abstractmethod
    def monotonic(self) -> float:
        """Monotonic seconds for measuring durations. Never goes backward."""
        ...

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Suspend until `seconds` have elapsed on this clock."""
        ...
