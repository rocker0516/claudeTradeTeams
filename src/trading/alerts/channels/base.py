"""Abstract AlertChannel — a single delivery destination for alerts.

Concrete implementations include LogChannel, TelegramChannel,
NtfyChannel, EmailChannel, etc. Channels are wired into AlertRouter
at registration time with a set of AlertLevels they should receive.

Channels MAY raise from send() on transient failures. The Router
catches these and continues to other channels — a single broken
channel must not block alerts to the rest.
"""

from abc import ABC, abstractmethod

from ...domain import Alert


class AlertChannel(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for this channel (used in logs / diagnostics)."""
        ...

    @abstractmethod
    async def send(self, alert: Alert) -> None:
        """Deliver `alert` to this channel's destination.

        May raise on transient failures (network, auth). Router catches.
        Implementations should be timeout-bounded — never hang.
        """
        ...

    @abstractmethod
    async def start(self) -> None:
        """Establish whatever connection / session the channel needs."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Tear down cleanly. Should flush any pending alerts where possible."""
        ...
