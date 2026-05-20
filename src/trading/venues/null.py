"""NullVenue — Venue that never trades, never holds positions.

Returns empty queries and refuses order placement. Useful for
infrastructure tests where Supervisor lifecycle must work end-to-end
without a real exchange connection.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal

from ..clock import Clock
from ..domain import Balance, Fill, Order, OrderId, OrderRequest, Position, Symbol
from .base import Venue


class NullVenue(Venue):
    """Venue stub with zero balance, no positions, no orders.

    place_order raises — callers should not attempt to trade through
    a NullVenue. This is louder than silently succeeding, which would
    mask wiring bugs.
    """

    def __init__(self, clock: Clock, *, currency: str = "USDT") -> None:
        self._clock = clock
        self._currency = currency

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def place_order(self, request: OrderRequest) -> OrderId:
        raise NotImplementedError("NullVenue does not accept orders")

    async def cancel_order(self, order_id: OrderId) -> bool:
        return False

    async def get_order(self, order_id: OrderId) -> Order | None:
        return None

    async def get_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        return []

    async def get_positions(self) -> list[Position]:
        return []

    async def get_balance(self) -> Balance:
        return Balance(
            currency=self._currency,
            total=Decimal("0"),
            available=Decimal("0"),
            margin_used=Decimal("0"),
            updated_at=self._clock.now(),
        )

    def on_order_update(self, callback: Callable[[Order], Awaitable[None]]) -> None:
        pass

    def on_fill(self, callback: Callable[[Fill], Awaitable[None]]) -> None:
        pass
