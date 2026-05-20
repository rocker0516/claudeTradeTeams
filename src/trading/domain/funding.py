"""Funding rate domain types (perpetual contracts)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .ids import Symbol


@dataclass(frozen=True, slots=True)
class FundingRate:
    symbol: Symbol
    rate: Decimal
    predicted_rate: Decimal | None
    next_funding_time: datetime
    timestamp: datetime
