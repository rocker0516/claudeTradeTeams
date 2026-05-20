"""HeartbeatBreaker — trips when a watched component goes silent.

Stateful breaker. Subscribes to Heartbeat events on construction and
tracks `last_seen` per component. On evaluate(), checks each watched
component's last_seen against `threshold`.

Components that have NEVER been seen are skipped — they're assumed to
not yet be running, not dead. The breaker watches for RUNTIME loss,
not initial wiring failures (those should surface louder, elsewhere).

Non-latching: clears the moment a fresh heartbeat arrives.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from ...domain import RiskContext, Trigger, TriggerAction
from ...runtime.event_bus import EventBus
from ...runtime.events import Heartbeat
from .base import CircuitBreaker


class HeartbeatBreaker(CircuitBreaker):
    """Watches a set of components for heartbeat liveness."""

    def __init__(
        self,
        *,
        bus: EventBus,
        components: Iterable[str],
        threshold: timedelta,
        action: TriggerAction = TriggerAction.QUARANTINE,
        breaker_name: str = "HeartbeatBreaker",
    ) -> None:
        self._components = set(components)
        self._threshold = threshold
        self._action = action
        self._name = breaker_name
        self._last_seen: dict[str, datetime] = {}
        bus.subscribe(Heartbeat, self._on_heartbeat)

    async def _on_heartbeat(self, heartbeat: Heartbeat) -> None:
        if heartbeat.component in self._components:
            self._last_seen[heartbeat.component] = heartbeat.timestamp

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        for component in self._components:
            last = self._last_seen.get(component)
            if last is None:
                # Never seen — skip. Not yet running, not stale.
                continue
            elapsed = context.now - last
            if elapsed > self._threshold:
                return Trigger(
                    breaker_name=self._name,
                    action=self._action,
                    reason=(
                        f"{component} silent for {elapsed.total_seconds():.1f}s "
                        f"(threshold {self._threshold.total_seconds()}s)"
                    ),
                )
        return None
