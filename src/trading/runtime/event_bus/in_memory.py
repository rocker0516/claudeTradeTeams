"""In-memory synchronous-dispatch EventBus.

Designed for single-task asyncio. Not thread-safe.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from .base import EventBus

T = TypeVar("T")

_logger = logging.getLogger(__name__)


class InMemoryEventBus(EventBus):
    """Synchronous in-process EventBus.

    On publish, dispatches to subscribers whose declared type matches
    via isinstance, in registration order. Awaits each callback in
    turn. Exceptions raised by an individual subscriber are caught
    and logged — they never break dispatch to other subscribers.

    Lifecycle:
        UNSTARTED -> RUNNING -> STOPPED

    Subscribe is allowed before start (wiring during build). publish
    only works in RUNNING. Once stopped, the bus cannot be restarted —
    matches the no-silent-recovery discipline the Supervisor follows.
    """

    def __init__(self) -> None:
        self._subs: list[tuple[type, Callable[[Any], Awaitable[None]]]] = []
        self._started = False
        self._stopped = False

    def subscribe(
        self,
        event_type: type[T],
        callback: Callable[[T], Awaitable[None]],
    ) -> None:
        if self._stopped:
            raise RuntimeError("EventBus is stopped; cannot subscribe")
        self._subs.append((event_type, cast("Callable[[Any], Awaitable[None]]", callback)))

    async def publish(self, event: object) -> None:
        if self._stopped:
            raise RuntimeError("EventBus stopped; cannot publish")
        if not self._started:
            raise RuntimeError("EventBus not started; cannot publish")
        for event_type, callback in self._subs:
            if isinstance(event, event_type):
                try:
                    await callback(event)
                except Exception:
                    _logger.exception("subscriber for %s raised", event_type.__name__)

    async def start(self) -> None:
        if self._stopped:
            raise RuntimeError("EventBus already stopped; cannot restart")
        self._started = True

    async def stop(self) -> None:
        self._started = False
        self._stopped = True
