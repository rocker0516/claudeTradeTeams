"""Abstract EventLog — append-only persistence for runtime events.

Generic store for any frozen-dataclass event with a `timestamp` field.
Implementations serialize via dataclass introspection (or a registered
codec) into a JSON payload plus an indexed type discriminator.

Read by type + time range for restart-time replay or for stateful
breaker reconstruction (e.g., ConsecutiveErrorBreaker on cold boot
reads recent ErrorOccurred events to seed its counter).

Schema sketch::

  events:
    id (pk), event_type (indexed, str class name), payload (json text),
    timestamp (indexed)

Append-only.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TypeVar

T = TypeVar("T")


class EventLog(ABC):
    @abstractmethod
    async def write(self, event: object) -> None:
        """Append `event` to the log.

        Implementations require `event` to be a frozen dataclass with a
        `timestamp: datetime` field (used to populate the indexed
        timestamp column).
        """
        ...

    @abstractmethod
    async def read_since(
        self,
        event_type: type[T],
        since: datetime,
    ) -> list[T]:
        """Return all events of type `event_type` with timestamp >= since,
        ordered by timestamp ascending. Deserialization is implementation-defined
        (typically a registered codec keyed on class name)."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
