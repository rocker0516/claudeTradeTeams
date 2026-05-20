"""Abstract MarketDataSource — live WebSocket or historical replay."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ..domain import FundingRate, OrderbookSnapshot, Symbol, Trade

OrderbookCallback = Callable[[OrderbookSnapshot], Awaitable[None]]
TradeCallback = Callable[[Trade], Awaitable[None]]
FundingCallback = Callable[[FundingRate], Awaitable[None]]


class MarketDataSource(ABC):
    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def subscribe_orderbook(self, symbol: Symbol, callback: OrderbookCallback) -> None:
        """Receive reconstructed orderbook snapshots after snapshot+diff
        sequencing. Implementations are responsible for resnapshotting
        on sequence gaps before invoking the callback."""
        ...

    @abstractmethod
    def subscribe_trades(self, symbol: Symbol, callback: TradeCallback) -> None: ...

    @abstractmethod
    def subscribe_funding(self, symbol: Symbol, callback: FundingCallback) -> None: ...
