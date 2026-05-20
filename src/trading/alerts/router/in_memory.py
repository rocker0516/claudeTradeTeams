"""In-memory AlertRouter — synchronous per-level fan-out."""

import logging

from ...domain import Alert, AlertLevel
from ..channels import AlertChannel
from .base import AlertRouter

_logger = logging.getLogger(__name__)


class InMemoryAlertRouter(AlertRouter):
    """Synchronous in-process AlertRouter.

    On alert(), iterates registered channels in registration order and
    sends to each whose level set includes the alert's level. Per-channel
    exceptions are caught and logged — a broken channel never blocks
    the others.

    Lifecycle: start() / stop() also propagate to all registered channels.
    """

    def __init__(self) -> None:
        self._registered: list[tuple[AlertChannel, tuple[AlertLevel, ...]]] = []

    def register(
        self,
        channel: AlertChannel,
        *,
        levels: tuple[AlertLevel, ...],
    ) -> None:
        self._registered.append((channel, levels))

    async def alert(self, alert: Alert) -> None:
        for channel, levels in self._registered:
            if alert.level in levels:
                try:
                    await channel.send(alert)
                except Exception:
                    _logger.exception("channel %s failed to send alert", channel.name)

    async def start(self) -> None:
        for channel, _ in self._registered:
            await channel.start()

    async def stop(self) -> None:
        for channel, _ in self._registered:
            await channel.stop()
