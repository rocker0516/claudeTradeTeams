"""LogChannel — AlertChannel backed by the standard logging module."""

import logging

from ...domain import Alert, AlertLevel
from .base import AlertChannel

_LEVEL_MAP = {
    AlertLevel.INFO: logging.INFO,
    AlertLevel.WARN: logging.WARNING,
    AlertLevel.ALERT: logging.ERROR,
    AlertLevel.CRITICAL: logging.CRITICAL,
}


class LogChannel(AlertChannel):
    """AlertChannel that emits alerts to a Python logger.

    The named logger can be configured via standard `logging.config`
    machinery in the application entrypoint — this class itself is
    deliberately small.
    """

    def __init__(
        self,
        *,
        channel_name: str = "log",
        logger_name: str = "trading.alerts",
    ) -> None:
        self._name = channel_name
        self._logger = logging.getLogger(logger_name)

    @property
    def name(self) -> str:
        return self._name

    async def send(self, alert: Alert) -> None:
        self._logger.log(
            _LEVEL_MAP[alert.level],
            "[%s] %s",
            alert.component,
            alert.message,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass
