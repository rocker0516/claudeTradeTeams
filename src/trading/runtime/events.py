"""Runtime events published on the EventBus.

Distinct from domain events (Fill, Order, OrderbookSnapshot, ...).
Runtime events describe the runtime's own behavior — state transitions,
breaker trips, heartbeats, errors — and are typically consumed by
alerts, monitoring, and the Supervisor itself.
"""

from dataclasses import dataclass
from datetime import datetime

from ..domain import TriggerAction
from .system_state import SystemState


@dataclass(frozen=True, slots=True)
class SystemStateChanged:
    """Emitted by Supervisor on every state transition."""

    old_state: SystemState
    new_state: SystemState
    reason: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class BreakerTripped:
    """Emitted when a CircuitBreaker fires.

    Supervisor subscribes to this and dispatches on `action`:
    REJECT_NEW / CANCEL_ALL / QUARANTINE / FLATTEN.
    """

    breaker_name: str
    action: TriggerAction
    reason: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Periodic liveness signal from a component.

    Consumed by HeartbeatBreaker — absence of heartbeat beyond threshold
    indicates a stalled component and trips the breaker.
    """

    component: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ReconciliationCompleted:
    """Emitted after a periodic OMS or PMS reconciliation.

    `drift_count` is the number of corrections applied. Non-zero is not
    inherently bad (network jitter happens) but persistent drift may
    indicate a deeper bug.
    """

    component: str
    drift_count: int
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ErrorOccurred:
    """Emitted when a component encounters an unexpected error.

    Consumed by ConsecutiveErrorBreaker.
    """

    component: str
    message: str
    exception_type: str
    timestamp: datetime
