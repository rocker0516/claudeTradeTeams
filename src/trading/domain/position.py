"""Position domain types."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .ids import Symbol


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class Position:
    symbol: Symbol
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    leverage: Decimal
    liquidation_price: Decimal | None
    updated_at: datetime
