"""Risk gate decision types.

A Decision is the result of RiskManager.check(intent). The hierarchy
is closed: only Approved or Rejected. Discriminate via isinstance or
pattern matching::

    match decision:
        case Approved():
            ...
        case Rejected(reason=r, breaker_name=b):
            ...
"""

from abc import ABC
from dataclasses import dataclass


class Decision(ABC):
    """Outcome of a risk check. Sealed: only Approved or Rejected."""


@dataclass(frozen=True, slots=True)
class Approved(Decision):
    """Risk gate cleared the intent."""


@dataclass(frozen=True, slots=True)
class Rejected(Decision):
    """Risk gate rejected the intent.

    `breaker_name` identifies which CircuitBreaker tripped, or None if
    rejected for a non-breaker reason (e.g. position limit, leverage cap).
    """

    reason: str
    breaker_name: str | None = None
