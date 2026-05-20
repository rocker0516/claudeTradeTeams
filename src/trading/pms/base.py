"""Abstract PositionManager (PMS).

Single in-process source of truth for "what do we own". Concrete
implementations subscribe to Venue events for incremental updates
and periodically call `reconcile()` to catch drift between local
cache and venue truth.

The venue is always the ultimate source of truth — the PMS cache
exists for latency, not for correctness.
"""

from abc import ABC, abstractmethod

from ..domain import Balance, Position, Symbol


class PositionManager(ABC):
    @abstractmethod
    async def positions(self) -> list[Position]: ...

    @abstractmethod
    async def position(self, symbol: Symbol) -> Position | None: ...

    @abstractmethod
    async def balance(self) -> Balance: ...

    @abstractmethod
    async def reconcile(self) -> None:
        """Force a sync with the venue. Resolves any drift between
        cached state and venue truth. Implementations should alert
        on non-trivial drift."""
        ...
