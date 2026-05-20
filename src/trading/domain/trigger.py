"""CircuitBreaker output types.

A Trigger is what a tripped breaker returns from evaluate(). The
TriggerAction tells the Supervisor how to react. Severity is strictly
hierarchical — each higher level implies the cumulative effect of
the lower ones:

    REJECT_NEW < CANCEL_ALL < QUARANTINE < FLATTEN

QUARANTINE does NOT imply FLATTEN. They are distinct cases:
  - QUARANTINE: trading session is unsafe, positions are fine
                (e.g., heartbeat loss — trying to flatten under a
                broken connection just adds chaos)
  - FLATTEN:    positions themselves are unsafe
                (e.g., drawdown breach — get out)
"""

from dataclasses import dataclass
from enum import Enum


class TriggerAction(str, Enum):
    """What the Supervisor should do when a breaker trips.

    Effects (cumulative — each level includes the prior):
      REJECT_NEW: reject new order submissions; keep existing state
      CANCEL_ALL: REJECT_NEW + cancel all open orders
      QUARANTINE: CANCEL_ALL + transition to QUARANTINE state
                  (positions intact, require human ack)
      FLATTEN:    QUARANTINE + close all open positions
    """

    REJECT_NEW = "reject_new"
    CANCEL_ALL = "cancel_all"
    QUARANTINE = "quarantine"
    FLATTEN = "flatten"


@dataclass(frozen=True, slots=True)
class Trigger:
    """Returned by CircuitBreaker.evaluate() when the breaker trips."""

    breaker_name: str
    action: TriggerAction
    reason: str
