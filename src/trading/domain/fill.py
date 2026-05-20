"""Fill domain types."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .ids import OrderId, Symbol
from .order import OrderSide


@dataclass(frozen=True, slots=True)
class Fill:
    """A single (partial or full) execution of an order."""

    order_id: OrderId
    symbol: Symbol
    side: OrderSide
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_currency: str
    is_maker: bool
    timestamp: datetime
