"""ConsecutiveErrorBreaker — trips on too many errors in a rolling window.

Stateful breaker. Subscribes to ErrorOccurred events and accumulates
timestamps. On evaluate(), prunes entries outside the window and
checks if the remaining count meets or exceeds threshold.

Non-latching: clears as errors age out of the window. For "trip
until human ack" semantics, wire the QUARANTINE action so Supervisor
latches the overall system state.

Optional component filter: if `components` is provided, only errors
from those components are counted. Omitting it means "count all".
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from ...domain import RiskContext, Trigger, TriggerAction
from ...runtime.event_bus import EventBus
from ...runtime.events import ErrorOccurred
from .base import CircuitBreaker


class ConsecutiveErrorBreaker(CircuitBreaker):
    """Trips when >= `threshold` errors occur within `window`."""

    def __init__(
        self,
        *,
        bus: EventBus,
        threshold: int,
        window: timedelta,
        components: Iterable[str] | None = None,
        action: TriggerAction = TriggerAction.QUARANTINE,
        breaker_name: str = "ConsecutiveErrorBreaker",
    ) -> None:
        if threshold < 1:
            raise ValueError(f"threshold must be >= 1, got {threshold}")
        self._threshold = threshold
        self._window = window
        self._components: set[str] | None = set(components) if components is not None else None
        self._action = action
        self._name = breaker_name
        self._error_times: list[datetime] = []
        bus.subscribe(ErrorOccurred, self._on_error)

    async def _on_error(self, error: ErrorOccurred) -> None:
        if self._components is None or error.component in self._components:
            self._error_times.append(error.timestamp)

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        cutoff = context.now - self._window
        # Prune in place to keep memory bounded under sustained error rates.
        # Mutation here is benign internal state — no external side effects.
        self._error_times = [t for t in self._error_times if t >= cutoff]

        if len(self._error_times) >= self._threshold:
            return Trigger(
                breaker_name=self._name,
                action=self._action,
                reason=(
                    f"{len(self._error_times)} errors in last "
                    f"{self._window.total_seconds():.1f}s "
                    f"(threshold {self._threshold})"
                ),
            )
        return None
