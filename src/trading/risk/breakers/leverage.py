"""LeverageBreaker — trips when any position's leverage exceeds threshold.

Pure stateless: only reads RiskContext.positions, no event subscription.

Default action is REJECT_NEW (stop opening new positions). Force-closing
to lower leverage is rarely the right move — leverage drops naturally
as PnL changes or as positions are reduced. Use FLATTEN action only if
combined with another condition (e.g., drawdown).

Non-latching: clears when leverage falls back below threshold.
"""

from decimal import Decimal

from ...domain import RiskContext, Trigger, TriggerAction
from .base import CircuitBreaker


class LeverageBreaker(CircuitBreaker):
    def __init__(
        self,
        *,
        max_leverage: Decimal,
        action: TriggerAction = TriggerAction.REJECT_NEW,
        breaker_name: str = "LeverageBreaker",
    ) -> None:
        if max_leverage <= 0:
            raise ValueError(f"max_leverage must be > 0, got {max_leverage}")
        self._max = max_leverage
        self._action = action
        self._name = breaker_name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        for pos in context.positions:
            if pos.leverage > self._max:
                return Trigger(
                    breaker_name=self._name,
                    action=self._action,
                    reason=(f"{pos.symbol} leverage {pos.leverage}x > {self._max}x"),
                )
        return None
