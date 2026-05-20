"""Abstract Strategy.

Event handlers for market and account events. Concrete strategies
receive their dependencies (executor facade, params, etc.) via
__init__ — the ABC does not prescribe a constructor, because
different strategies need different inputs.

A strategy never calls a Venue directly — it goes through an
Executor facade (defined elsewhere) that wraps RiskManager checks
and OMS submission.
"""

from abc import ABC, abstractmethod

from ..domain import (
    Fill,
    FundingRate,
    Order,
    OrderbookSnapshot,
    Trade,
)


class Strategy(ABC):
    @abstractmethod
    async def on_orderbook(self, snapshot: OrderbookSnapshot) -> None: ...

    @abstractmethod
    async def on_trade(self, trade: Trade) -> None: ...

    @abstractmethod
    async def on_funding(self, rate: FundingRate) -> None: ...

    @abstractmethod
    async def on_fill(self, fill: Fill) -> None: ...

    @abstractmethod
    async def on_order_update(self, order: Order) -> None: ...
