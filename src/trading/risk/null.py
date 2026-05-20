"""NullRiskManager — approves everything. Unsafe for real money."""

from ..domain import Approved, Decision, TradeIntent
from .base import RiskManager


class NullRiskManager(RiskManager):
    """RiskManager stub that approves every intent and never trips.

    DANGER: never use with real money. Intended for infrastructure
    boot/shutdown tests and integration tests where the strategy is
    NullStrategy (never trades anyway). Production needs a real
    RiskManager wired with CircuitBreakers.
    """

    async def check(self, intent: TradeIntent) -> Decision:
        return Approved()

    async def tick(self) -> None:
        pass
