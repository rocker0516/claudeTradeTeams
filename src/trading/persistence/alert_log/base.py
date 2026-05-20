"""Abstract AlertLog — append-only persistence for emitted alerts.

Records every Alert sent through AlertRouter for audit / debugging.
Distinct from EventLog because (a) Alerts have a fixed schema and
(b) they are queryable by component / correlation_key for grouping.

Schema sketch::

  alerts:
    id (pk), level, component (indexed), message,
    correlation_key (indexed, nullable), timestamp (indexed)

Append-only.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from ...domain import Alert, AlertLevel


class AlertLog(ABC):
    @abstractmethod
    async def write(self, alert: Alert) -> None: ...

    @abstractmethod
    async def read_since(
        self,
        since: datetime,
        *,
        level: AlertLevel | None = None,
        component: str | None = None,
    ) -> list[Alert]:
        """Return alerts since `since`, optionally filtered by level
        and/or component, ordered by timestamp ascending."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
