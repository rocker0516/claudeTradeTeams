"""Public market data types."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .ids import Symbol
from .order import OrderSide


@dataclass(frozen=True, slots=True)
class Level:
    """A single price level in an orderbook."""

    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderbookSnapshot:
    symbol: Symbol
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    sequence: int
    timestamp: datetime

    @property
    def best_bid(self) -> Level | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Level | None:
        return self.asks[0] if self.asks else None


@dataclass(frozen=True, slots=True)
class Trade:
    """A single market trade observed in the public tape."""

    symbol: Symbol
    price: Decimal
    quantity: Decimal
    side: OrderSide
    timestamp: datetime
