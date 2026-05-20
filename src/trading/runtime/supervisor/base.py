"""Abstract Supervisor — top-level lifecycle orchestrator.

Owns the SystemState machine, composes every component (Venue, MDS,
OMS, PMS, RiskManager, Strategy, Executor, EventBus, Persistence,
Alerts), and runs the main event loop.

Concrete responsibilities (left to implementations):
  - Wire components via EventBus and direct callbacks
  - Drive periodic ticks (reconciliation, RiskManager.tick, heartbeat)
  - Subscribe to BreakerTripped and transition to QUARANTINE
  - Handle SIGTERM / SIGINT for graceful shutdown
  - Persist state changes so restart can resume cleanly
"""

from abc import ABC, abstractmethod

from ..system_state import SystemState


class Supervisor(ABC):
    @abstractmethod
    async def start(self) -> None:
        """Boot sequence:

        1. Load config + secrets
        2. Build component graph (composition root)
        3. Restore persisted state from DB
        4. Initial reconciliation (must succeed or -> QUARANTINE)
        5. Subscribe to events; start all Lifecycle components
        6. Transition BOOTING -> RECONCILING -> RUNNING
        """
        ...

    @abstractmethod
    async def stop(self, *, drain: bool = True) -> None:
        """Graceful shutdown.

        If drain=True, cancel open orders (and optionally flatten
        positions) before exiting. If False, leave positions intact
        and just disconnect cleanly. Always flushes persistence.
        """
        ...

    @abstractmethod
    async def quarantine(
        self,
        reason: str,
        *,
        breaker_name: str | None = None,
    ) -> None:
        """Transition RUNNING -> QUARANTINE.

        Effects:
          - Stops accepting new order submissions
          - Emits CRITICAL alert
          - Awaits human acknowledge() before any further trading

        Does NOT close existing positions by default — that decision
        belongs to the CircuitBreaker's action field (future iteration).
        """
        ...

    @abstractmethod
    async def acknowledge(self) -> None:
        """Human ack to exit QUARANTINE.

        Triggers re-reconciliation. If reconciliation succeeds,
        returns to RUNNING. Otherwise stays in QUARANTINE.
        """
        ...

    @property
    @abstractmethod
    def state(self) -> SystemState: ...
