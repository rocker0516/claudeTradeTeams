"""RateLimitBreaker — trips on N rate-limit errors in window, auto-cools.

Stateful. Subscribes to ErrorOccurred and filters by exception_type
substring (default `"RateLimit"` — matches `RateLimitError`,
`HTTPRateLimit`, etc. from various clients).

When threshold is crossed inside `window`, trips and stays tripped for
`cooldown` — even if more errors arrive, no new orders can be placed.
After cooldown expires, the internal counter resets and the breaker
clears.

This is the only breaker with **auto-clearing latch** semantic:
neither pure non-latching nor human-ack-only — latched for a fixed
duration. Acceptable here because rate-limit errors are transient and
self-resolve once the system stops hammering the API.

Default action is REJECT_NEW. The actual API call backoff lives in
the Venue layer (BybitVenue, TBD). This breaker is a meta-signal that
the system is unhealthy — strategy should pause.
"""

from datetime import datetime, timedelta

from ...domain import RiskContext, Trigger, TriggerAction
from ...runtime.event_bus import EventBus
from ...runtime.events import ErrorOccurred
from .base import CircuitBreaker


class RateLimitBreaker(CircuitBreaker):
    def __init__(
        self,
        *,
        bus: EventBus,
        threshold: int = 3,
        window: timedelta = timedelta(seconds=10),
        cooldown: timedelta = timedelta(minutes=5),
        exception_type_match: str = "RateLimit",
        action: TriggerAction = TriggerAction.REJECT_NEW,
        breaker_name: str = "RateLimitBreaker",
    ) -> None:
        if threshold < 1:
            raise ValueError(f"threshold must be >= 1, got {threshold}")
        self._threshold = threshold
        self._window = window
        self._cooldown = cooldown
        self._match = exception_type_match
        self._action = action
        self._name = breaker_name

        self._error_times: list[datetime] = []
        self._tripped_until: datetime | None = None

        bus.subscribe(ErrorOccurred, self._on_error)

    async def _on_error(self, error: ErrorOccurred) -> None:
        if self._match in error.exception_type:
            self._error_times.append(error.timestamp)

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        # Still in cooldown from a previous trip → keep tripping
        if self._tripped_until is not None:
            if context.now < self._tripped_until:
                remaining = (self._tripped_until - context.now).total_seconds()
                return Trigger(
                    breaker_name=self._name,
                    action=self._action,
                    reason=f"rate-limit cooldown active ({remaining:.0f}s remaining)",
                )
            # Cooldown expired → reset
            self._tripped_until = None
            self._error_times.clear()

        # Fresh evaluation: count errors in window
        cutoff = context.now - self._window
        self._error_times = [t for t in self._error_times if t >= cutoff]
        if len(self._error_times) >= self._threshold:
            self._tripped_until = context.now + self._cooldown
            return Trigger(
                breaker_name=self._name,
                action=self._action,
                reason=(
                    f"{len(self._error_times)} rate-limit errors in "
                    f"{self._window.total_seconds():.0f}s — cooldown for "
                    f"{self._cooldown.total_seconds():.0f}s"
                ),
            )
        return None
