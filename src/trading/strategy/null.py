"""NullStrategy — Strategy that never trades.

Used for idle-mode infrastructure testing and as the default for any
runtime configuration where no alpha is wired up. All event callbacks
are no-ops.
"""

from ..domain import Fill, FundingRate, Order, OrderbookSnapshot, Trade
from .base import Strategy


class NullStrategy(Strategy):
    """Strategy that ignores every event. Produces no trades."""

    async def on_orderbook(self, snapshot: OrderbookSnapshot) -> None:
        pass

    async def on_trade(self, trade: Trade) -> None:
        pass

    async def on_funding(self, rate: FundingRate) -> None:
        pass

    async def on_fill(self, fill: Fill) -> None:
        pass

    async def on_order_update(self, order: Order) -> None:
        pass
