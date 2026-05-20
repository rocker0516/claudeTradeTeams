"""In-memory AlertLog — list-backed."""

from datetime import datetime

from ...domain import Alert, AlertLevel
from .base import AlertLog


class InMemoryAlertLog(AlertLog):
    """List-backed AlertLog for tests and POC."""

    def __init__(self) -> None:
        self._alerts: list[Alert] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def write(self, alert: Alert) -> None:
        self._alerts.append(alert)

    async def read_since(
        self,
        since: datetime,
        *,
        level: AlertLevel | None = None,
        component: str | None = None,
    ) -> list[Alert]:
        result = [a for a in self._alerts if a.timestamp >= since]
        if level is not None:
            result = [a for a in result if a.level == level]
        if component is not None:
            result = [a for a in result if a.component == component]
        return result
