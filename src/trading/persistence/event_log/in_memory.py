"""In-memory EventLog — list-backed."""

from datetime import datetime
from typing import TypeVar

from .base import EventLog

T = TypeVar("T")


class InMemoryEventLog(EventLog):
    """List-backed EventLog for tests and POC.

    Events must have a `timestamp` attribute (typically a frozen
    dataclass with `timestamp: datetime`). write() validates this
    upfront so bad writes fail loudly rather than corrupting reads.
    """

    def __init__(self) -> None:
        self._events: list[object] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def write(self, event: object) -> None:
        if not hasattr(event, "timestamp"):
            raise TypeError(f"event must have a 'timestamp' field; got {type(event).__name__}")
        self._events.append(event)

    async def read_since(
        self,
        event_type: type[T],
        since: datetime,
    ) -> list[T]:
        result: list[T] = []
        for e in self._events:
            if not isinstance(e, event_type):
                continue
            ts = getattr(e, "timestamp", None)
            if ts is None or ts < since:
                continue
            result.append(e)
        return result
