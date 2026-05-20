"""NullPositionManager — empty positions, zero balance."""

from decimal import Decimal

from ..clock import Clock
from ..domain import Balance, Position, Symbol
from .base import PositionManager


class NullPositionManager(PositionManager):
    """PMS stub reporting no positions and a zero balance."""

    def __init__(self, clock: Clock, *, currency: str = "USDT") -> None:
        self._clock = clock
        self._currency = currency

    async def positions(self) -> list[Position]:
        return []

    async def position(self, symbol: Symbol) -> Position | None:
        return None

    async def balance(self) -> Balance:
        return Balance(
            currency=self._currency,
            total=Decimal("0"),
            available=Decimal("0"),
            margin_used=Decimal("0"),
            updated_at=self._clock.now(),
        )

    async def reconcile(self) -> None:
        pass
