"""Risk evaluation context.

Immutable snapshot of system state assembled by RiskManager once
per tick and passed to every CircuitBreaker. Aggregating state into
a single value lets stateless breakers be pure functions of context
and makes evaluation reproducible / testable.

Stateful breakers (e.g., ConsecutiveErrorBreaker tracking a counter)
maintain their own state via EventBus subscriptions set up in
__init__ and read it inside evaluate().
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from .balance import Balance
from .funding import FundingRate
from .ids import Symbol
from .order import Order
from .position import Position


@dataclass(frozen=True, slots=True)
class RiskContext:
    now: datetime
    positions: tuple[Position, ...]
    balance: Balance
    open_orders: tuple[Order, ...]
    funding_rates: Mapping[Symbol, FundingRate]
