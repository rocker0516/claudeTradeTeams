"""Account balance domain types."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Balance:
    currency: str
    total: Decimal
    available: Decimal
    margin_used: Decimal
    updated_at: datetime
