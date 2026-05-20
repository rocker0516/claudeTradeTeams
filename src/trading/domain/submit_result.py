"""Outcome of Executor.submit_intent.

The four cases are deliberately distinct because strategies must
handle them differently:

  - Submitted          -> track this order_id from here on
  - RiskRejected       -> terminal for this intent, do not retry
  - VenueRejected      -> usually a size / price / symbol rule problem;
                          fix and re-submit if appropriate
  - SubmissionFailed   -> transport-level failure. If is_retryable,
                          re-submit with the same client_order_id
                          (venue idempotency dedupes). Otherwise the
                          state is unknown — reconciliation resolves it.
"""

from abc import ABC
from dataclasses import dataclass

from .ids import ClientOrderId, OrderId


class SubmitResult(ABC):
    """Outcome of an Executor.submit_intent call. Sealed."""


@dataclass(frozen=True, slots=True)
class Submitted(SubmitResult):
    order_id: OrderId
    client_order_id: ClientOrderId


@dataclass(frozen=True, slots=True)
class RiskRejected(SubmitResult):
    reason: str
    breaker_name: str | None = None


@dataclass(frozen=True, slots=True)
class VenueRejected(SubmitResult):
    reason: str


@dataclass(frozen=True, slots=True)
class SubmissionFailed(SubmitResult):
    """Transport-level failure (network / timeout / unknown state)."""

    reason: str
    is_retryable: bool
