"""NullMarketDataSource — accepts subscriptions, emits nothing."""

from ..domain import Symbol
from .base import (
    FundingCallback,
    MarketDataSource,
    OrderbookCallback,
    TradeCallback,
)


class NullMarketDataSource(MarketDataSource):
    """MarketDataSource that registers subscriptions but never fires them.

    Useful for idle-mode infrastructure tests. Subscribers receive no
    events, so any strategy attached will never get a chance to act.
    """

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def subscribe_orderbook(self, symbol: Symbol, callback: OrderbookCallback) -> None:
        pass

    def subscribe_trades(self, symbol: Symbol, callback: TradeCallback) -> None:
        pass

    def subscribe_funding(self, symbol: Symbol, callback: FundingCallback) -> None:
        pass
