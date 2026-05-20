"""LiquidationDistanceBreaker — trips when position is too close to liquidation.

Pure stateless. For each position, computes
    distance = |mark_price - liquidation_price| / mark_price
If any position's distance falls below threshold → trip.

Positions with `liquidation_price is None` (cross-margin with no risk
of liquidation) are skipped. Same for mark_price <= 0 (degenerate).

Default action is FLATTEN — distance to liquidation IS the "position
itself is unsafe" case. Real-life option is partial flatten (e.g.,
reduce 50%) but TriggerAction doesn't model partial; use FLATTEN and
let Supervisor decide reduction strategy (TBD).

Non-latching: clears when distance recovers.
"""

from decimal import Decimal

from ...domain import RiskContext, Trigger, TriggerAction
from .base import CircuitBreaker


class LiquidationDistanceBreaker(CircuitBreaker):
    def __init__(
        self,
        *,
        min_distance: Decimal,  # e.g., Decimal("0.20") for 20%
        action: TriggerAction = TriggerAction.FLATTEN,
        breaker_name: str = "LiquidationDistanceBreaker",
    ) -> None:
        if min_distance <= 0:
            raise ValueError(f"min_distance must be > 0, got {min_distance}")
        self._min_distance = min_distance
        self._action = action
        self._name = breaker_name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        for pos in context.positions:
            if pos.liquidation_price is None:
                continue
            if pos.mark_price <= 0:
                continue
            distance = abs(pos.mark_price - pos.liquidation_price) / pos.mark_price
            if distance < self._min_distance:
                return Trigger(
                    breaker_name=self._name,
                    action=self._action,
                    reason=(
                        f"{pos.symbol} liq distance {distance:.2%} "
                        f"< {self._min_distance:.2%} "
                        f"(mark {pos.mark_price}, liq {pos.liquidation_price})"
                    ),
                )
        return None
