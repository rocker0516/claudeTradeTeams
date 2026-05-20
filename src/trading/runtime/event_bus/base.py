"""Abstract EventBus — type-safe pub/sub for runtime events.

Subscribers register a callback against an event type. Publishers
emit events. The bus dispatches to every subscriber whose declared
type matches via isinstance (so subscribing to a base class catches
subclasses too).

Contracts on implementations:
  - Multiple subscribers per type are allowed
  - Exceptions raised by an individual subscriber MUST NOT break
    dispatch to other subscribers (catch + log per callback)
  - Dispatch order is implementation-defined; callers must not rely
    on FIFO across subscribers
  - publish() may be synchronous-and-immediate or queued-and-async
    per implementation
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class EventBus(ABC):
    @abstractmethod
    def subscribe(
        self,
        event_type: type[T],
        callback: Callable[[T], Awaitable[None]],
    ) -> None:
        """Register `callback` for events of type `event_type` (or subclasses)."""
        ...

    @abstractmethod
    async def publish(self, event: object) -> None:
        """Publish an event. Subscribers whose declared type matches
        (via isinstance) are invoked."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Begin accepting publish() calls."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop accepting publishes. Pending events may be drained or
        dropped per implementation."""
        ...
