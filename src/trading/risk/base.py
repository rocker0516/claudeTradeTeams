"""Abstract RiskManager.

Two responsibilities:

1. Pre-trade gate: `check(intent)` evaluates a TradeIntent against
   position limits, leverage caps, and active CircuitBreakers.
   Returns a Decision (Approved or Rejected).

2. Continuous monitoring: `tick()` re-evaluates all breakers against
   current state. May transition the system to QUARANTINE regardless
   of any incoming intent (e.g. drawdown breach, heartbeat loss).

Concrete implementations maintain internal state (positions, balance,
heartbeat, error count) via event subscriptions set up in __init__.
"""

from abc import ABC, abstractmethod

from ..domain import Decision, TradeIntent


class RiskManager(ABC):
    @abstractmethod
    async def check(self, intent: TradeIntent) -> Decision:
        """Pre-trade gate. Returns Approved or Rejected with a reason."""
        ...

    @abstractmethod
    async def tick(self) -> None:
        """Periodic re-evaluation. Runs all CircuitBreakers against
        current state and may trip the system to QUARANTINE."""
        ...
