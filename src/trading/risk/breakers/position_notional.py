"""PositionNotionalBreaker — trips on any position exceeding a USD cap.

Pure stateless. Notional is computed as abs(quantity) * mark_price per
position.

Default action is REJECT_NEW. Force-closing isn't appropriate — once
you have a too-large position, reducing it requires a separate decision
(may incur loss). The breaker prevents *growing* the position further.

Non-latching: clears when notional falls back below cap (price moves
or partial close).
"""

from decimal import Decimal

from ...domain import RiskContext, Trigger, TriggerAction
from .base import CircuitBreaker


class PositionNotionalBreaker(CircuitBreaker):
    def __init__(
        self,
        *,
        max_notional_usd: Decimal,
        action: TriggerAction = TriggerAction.REJECT_NEW,
        breaker_name: str = "PositionNotionalBreaker",
    ) -> None:
        if max_notional_usd <= 0:
            raise ValueError(f"max_notional_usd must be > 0, got {max_notional_usd}")
        self._max = max_notional_usd
        self._action = action
        self._name = breaker_name

    @property
    def name(self) -> str:
        return self._name

    def evaluate(self, context: RiskContext) -> Trigger | None:
        for pos in context.positions:
            notional = abs(pos.quantity) * pos.mark_price
            if notional > self._max:
                return Trigger(
                    breaker_name=self._name,
                    action=self._action,
                    reason=(f"{pos.symbol} notional {notional} > {self._max}"),
                )
        return None
