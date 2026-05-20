"""DrawdownBreaker — latching breaker on peak-to-trough equity loss.

The FIRST latching breaker in the system. Once tripped, stays tripped
until cleared, regardless of subsequent equity recovery — this is
the right semantic for a drawdown breach (the strategy already proved
unsafe; auto-resuming would be dangerous).

Un-latch mechanism: subscribes to SystemStateChanged. When it observes
QUARANTINE -> RECONCILING (i.e. Supervisor.acknowledge() was called
by a human), it clears the latched state AND the equity history. The
peak resets fresh from that point.

This design intentionally avoids adding a reset() method to the
CircuitBreaker ABC — the EventBus is sufficient. Any future latching
breaker can subscribe to the same signal.

Equity is read from `context.balance.total`. The data source is
expected to roll unrealized PnL into the total (matches Bybit Unified
Trading Account semantics). If a venue reports cash-only balance,
that should be normalized at the venue / PMS boundary, not here.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from ...domain import RiskContext, Trigger, TriggerAction
from ...runtime.event_bus import EventBus
from ...runtime.events import SystemStateChanged
from ...runtime.system_state import SystemState
from .base import CircuitBreaker


class DrawdownBreaker(CircuitBreaker):
    """Latching breaker tripping on peak-to-current drawdown over a window."""

    def __init__(
        self,
        *,
        bus: EventBus,
        threshold: Decimal,
        window: timedelta,
        action: TriggerAction = TriggerAction.FLATTEN,
        breaker_name: str = "DrawdownBreaker",
    ) -> None:
        if threshold <= 0:
            raise ValueError(f"threshold must be > 0, got {threshold}")
        self._threshold = threshold
        self._window = window
        self._action = action
        self._name = breaker_name

        self._equity_history: list[tuple[datetime, Decimal]] = []
        self._latched = False
        self._latched_reason = ""

        bus.subscribe(SystemStateChanged, self._on_state_change)

    async def _on_state_change(self, event: SystemStateChanged) -> None:
        """Reset latch on QUARANTINE -> RECONCILING (human acknowledge)."""
        if event.old_state == SystemState.QUARANTINE and event.new_state == SystemState.RECONCILING:
            self._latched = False
            self._latched_reason = ""
            self._equity_history.clear()

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        if self._latched:
            return Trigger(
                breaker_name=self._name,
                action=self._action,
                reason=f"latched: {self._latched_reason}",
            )

        equity = context.balance.total

        # Record + prune
        self._equity_history.append((context.now, equity))
        cutoff = context.now - self._window
        self._equity_history = [(t, e) for t, e in self._equity_history if t >= cutoff]

        peak = max(e for _, e in self._equity_history)
        if peak <= 0:
            # Can't compute meaningful drawdown without positive peak.
            return None

        drawdown = (peak - equity) / peak
        if drawdown > self._threshold:
            self._latched = True
            self._latched_reason = (
                f"drawdown {drawdown:.2%} (peak {peak}, current {equity}, "
                f"threshold {self._threshold:.2%})"
            )
            return Trigger(
                breaker_name=self._name,
                action=self._action,
                reason=self._latched_reason,
            )
        return None
