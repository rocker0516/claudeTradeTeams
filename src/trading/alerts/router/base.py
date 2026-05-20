"""Abstract AlertRouter.

Receives Alerts via alert() and fans them out to registered channels
based on AlertLevel. Deliberately decoupled from EventBus — runtime
events do not flow into the Router directly. The mapping from
domain/runtime events to alerts is the responsibility of Supervisor
or a dedicated adapter.

Implementations are expected to:
  - Catch all per-channel exceptions (one bad channel must not block
    the others)
  - Deduplicate identical (level, correlation_key) alerts within a
    short window when correlation_key is set
  - Bound per-channel timeouts so a slow channel doesn't stall others
  - Optionally rate-limit / batch by level (WARN may be aggregated;
    CRITICAL must always be delivered immediately)
"""

from abc import ABC, abstractmethod

from ...domain import Alert, AlertLevel
from ..channels import AlertChannel


class AlertRouter(ABC):
    @abstractmethod
    async def alert(self, alert: Alert) -> None:
        """Route `alert` to every channel registered for its level.

        Per-channel exceptions are caught and logged — never re-raised.
        """
        ...

    @abstractmethod
    def register(
        self,
        channel: AlertChannel,
        *,
        levels: tuple[AlertLevel, ...],
    ) -> None:
        """Register `channel` to receive alerts of the specified levels.

        Registration is sync (config wiring only). The channel's own
        connection lifecycle is handled by start() / stop().
        """
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start the router and all registered channels."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the router and all registered channels. Flushes pending alerts."""
        ...
