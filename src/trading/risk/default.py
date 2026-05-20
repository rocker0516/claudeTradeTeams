"""DefaultRiskManager — orchestrates breakers + publishes BreakerTripped.

On `check(intent)`, builds a RiskContext snapshot and evaluates every
breaker. If any trips, returns Rejected with that trigger's reason
and breaker_name. Otherwise Approved.

On `tick()`, builds a RiskContext and evaluates breakers; any tripped
breakers publish a BreakerTripped event on the bus (Supervisor
subscribes and dispatches by action).
"""

from collections.abc import Iterable, Mapping

from ..clock import Clock
from ..domain import (
    Approved,
    Decision,
    FundingRate,
    Rejected,
    RiskContext,
    Symbol,
    TradeIntent,
)
from ..oms import OrderManager
from ..pms import PositionManager
from ..runtime.event_bus import EventBus
from ..runtime.events import BreakerTripped
from .base import RiskManager
from .breakers import CircuitBreaker

_EMPTY_FUNDING: Mapping[Symbol, FundingRate] = {}


class DefaultRiskManager(RiskManager):
    """Concrete RiskManager. check() returns first trigger as Rejected;
    tick() publishes one BreakerTripped per tripped breaker.

    `funding_rates` in the context is currently always empty — funding
    rate caching is a future concern (the breaker that consumes it,
    FundingSpikeBreaker, doesn't exist yet).
    """

    def __init__(
        self,
        *,
        clock: Clock,
        bus: EventBus,
        oms: OrderManager,
        pms: PositionManager,
        breakers: Iterable[CircuitBreaker],
    ) -> None:
        self._clock = clock
        self._bus = bus
        self._oms = oms
        self._pms = pms
        self._breakers = list(breakers)

    async def check(self, intent: TradeIntent) -> Decision:
        context = await self._build_context()
        for breaker in self._breakers:
            trigger = breaker.evaluate(context)
            if trigger is not None:
                return Rejected(
                    reason=trigger.reason,
                    breaker_name=trigger.breaker_name,
                )
        return Approved()

    async def tick(self) -> None:
        context = await self._build_context()
        for breaker in self._breakers:
            trigger = breaker.evaluate(context)
            if trigger is None:
                continue
            await self._bus.publish(
                BreakerTripped(
                    breaker_name=trigger.breaker_name,
                    action=trigger.action,
                    reason=trigger.reason,
                    timestamp=self._clock.now(),
                )
            )

    async def _build_context(self) -> RiskContext:
        positions = tuple(await self._pms.positions())
        balance = await self._pms.balance()
        open_orders = tuple(await self._oms.open_orders())
        return RiskContext(
            now=self._clock.now(),
            positions=positions,
            balance=balance,
            open_orders=open_orders,
            funding_rates=_EMPTY_FUNDING,
        )
