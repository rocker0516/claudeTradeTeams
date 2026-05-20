"""Alert domain types.

An Alert is what flows through AlertRouter to AlertChannels. The
AlertLevel determines which channels see it (configured at register
time on the Router).

`correlation_key` is intended for deduplication. Routers that
implement dedup typically suppress identical (level, correlation_key)
alerts within a configured window. Use stable keys like
'breaker:DrawdownBreaker' rather than per-event UUIDs.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AlertLevel(str, Enum):
    """Severity / routing level.

    Strictly ordered: INFO < WARN < ALERT < CRITICAL. Higher levels
    typically reach more channels (and the more disruptive ones).
    """

    INFO = "info"
    WARN = "warn"
    ALERT = "alert"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Alert:
    level: AlertLevel
    component: str
    message: str
    timestamp: datetime
    correlation_key: str | None = None
