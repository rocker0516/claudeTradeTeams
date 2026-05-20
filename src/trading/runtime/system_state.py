"""System-level state machine.

Distinct from order state (domain/order.py). This tracks the runtime's
overall posture and is owned by the Supervisor.

Allowed transitions::

    BOOTING -> RECONCILING -> RUNNING
    RECONCILING -> QUARANTINE       (initial reconcile fails)
    RUNNING -> QUARANTINE           (BreakerTripped event)
    RUNNING -> DRAINING             (graceful shutdown w/ drain)
    RUNNING -> STOPPED              (graceful shutdown w/o drain)
    QUARANTINE -> RECONCILING       (human acknowledge() only)
    QUARANTINE -> DRAINING          (human shutdown)
    DRAINING -> STOPPED             (cleanup complete)

No path returns from QUARANTINE to RUNNING without an explicit
acknowledge() call — the system never silently decides it's fine again.
"""

from enum import Enum


class SystemState(str, Enum):
    BOOTING = "booting"
    RECONCILING = "reconciling"
    RUNNING = "running"
    QUARANTINE = "quarantine"
    DRAINING = "draining"
    STOPPED = "stopped"
