"""FundingSpikeBreaker — trips on abnormally high funding rate.

Pure stateless. Reads `context.funding_rates: Mapping[Symbol, FundingRate]`
and trips when any tracked symbol's |rate| exceeds threshold.

CAVEAT: DefaultRiskManager currently always sets `funding_rates` to an
empty mapping. This breaker is effectively no-op until funding rate
caching is wired up (Phase 1 funding strategy will need this anyway —
add a funding_rate subscriber on MarketDataSource that populates
RiskManager's cache).

Default action is REJECT_NEW (stop opening new positions during the
spike — existing ones ride out the funding period; closing mid-period
would still pay funding plus take spread cost on exit).

Non-latching: clears when funding normalizes.

Optional `symbols` filter restricts which symbols are checked; default
None means "all symbols present in funding_rates".
"""

from collections.abc import Iterable
from decimal import Decimal

from ...domain import RiskContext, Symbol, Trigger, TriggerAction
from .base import CircuitBreaker


class FundingSpikeBreaker(CircuitBreaker):
    def __init__(
        self,
        *,
        max_abs_rate: Decimal,  # e.g., Decimal("0.005") for 0.5% per funding interval
        symbols: Iterable[Symbol] | None = None,
        action: TriggerAction = TriggerAction.REJECT_NEW,
        breaker_name: str = "FundingSpikeBreaker",
    ) -> None:
        if max_abs_rate <= 0:
            raise ValueError(f"max_abs_rate must be > 0, got {max_abs_rate}")
        self._max = max_abs_rate
        self._symbols: set[Symbol] | None = set(symbols) if symbols is not None else None
        self._action = action
        self._name = breaker_name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        for symbol, fr in context.funding_rates.items():
            if self._symbols is not None and symbol not in self._symbols:
                continue
            if abs(fr.rate) > self._max:
                return Trigger(
                    breaker_name=self._name,
                    action=self._action,
                    reason=(f"{symbol} funding {fr.rate:.4%} (|.|) > {self._max:.4%}"),
                )
        return None
