"""Abstract Venue.

Public market data (orderbook, trades, funding) goes through
MarketDataSource, not Venue. Venue covers:
  - Order operations: place / cancel / query
  - Account state: positions, balance
  - Private push events: order updates, fills

This split lets PaperVenue and BacktestVenue reuse the same
MarketDataSource implementation while substituting a mock fill engine
for private events.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ..domain import (
    Balance,
    Fill,
    Order,
    OrderId,
    OrderRequest,
    Position,
    Symbol,
)

OrderUpdateCallback = Callable[[Order], Awaitable[None]]
FillCallback = Callable[[Fill], Awaitable[None]]


class Venue(ABC):
    @abstractmethod
    async def start(self) -> None:
        """Connect, authenticate, subscribe private channels."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect cleanly."""
        ...

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderId:
        """Submit an order. Must be idempotent on `request.client_order_id` —
        replaying the same client_order_id within the venue's dedup window
        must return the same OrderId without placing a second order."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: OrderId) -> bool:
        """Returns True if the cancel was accepted by the venue."""
        ...

    @abstractmethod
    async def get_order(self, order_id: OrderId) -> Order | None: ...

    @abstractmethod
    async def get_open_orders(self, symbol: Symbol | None = None) -> list[Order]: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_balance(self) -> Balance: ...

    @abstractmethod
    def on_order_update(self, callback: OrderUpdateCallback) -> None:
        """Register a callback for private order state changes."""
        ...

    @abstractmethod
    def on_fill(self, callback: FillCallback) -> None:
        """Register a callback for fills (partial or full)."""
        ...
