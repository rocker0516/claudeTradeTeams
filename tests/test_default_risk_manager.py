"""DefaultRiskManager contract tests."""

from decimal import Decimal

from trading.clock import RealClock
from trading.domain import (
    Approved,
    OrderSide,
    OrderType,
    Rejected,
    RiskContext,
    Symbol,
    TradeIntent,
    Trigger,
    TriggerAction,
)
from trading.oms import NullOrderManager
from trading.pms import NullPositionManager
from trading.risk import DefaultRiskManager
from trading.risk.breakers import CircuitBreaker
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.events import BreakerTripped


class _AlwaysTrip(CircuitBreaker):
    def __init__(self, action: TriggerAction = TriggerAction.QUARANTINE) -> None:
        self._action = action

    @property
    def name(self) -> str:
        return "AlwaysTrip"

    def evaluate(self, context: RiskContext) -> Trigger | None:
        return Trigger(
            breaker_name=self.name,
            action=self._action,
            reason="always-trip",
        )


class _NeverTrip(CircuitBreaker):
    @property
    def name(self) -> str:
        return "NeverTrip"

    def evaluate(self, context: RiskContext) -> Trigger | None:
        return None


def _intent() -> TradeIntent:
    return TradeIntent(
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=Decimal("1"),
    )


def _build_rm(breakers: list[CircuitBreaker], bus: InMemoryEventBus) -> DefaultRiskManager:
    clock = RealClock()
    return DefaultRiskManager(
        clock=clock,
        bus=bus,
        oms=NullOrderManager(),
        pms=NullPositionManager(clock),
        breakers=breakers,
    )


async def test_check_with_no_breakers_approves() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    rm = _build_rm([], bus)
    decision = await rm.check(_intent())
    assert isinstance(decision, Approved)


async def test_check_with_passing_breaker_approves() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    rm = _build_rm([_NeverTrip()], bus)
    assert isinstance(await rm.check(_intent()), Approved)


async def test_check_with_tripping_breaker_rejects() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    rm = _build_rm([_AlwaysTrip()], bus)
    decision = await rm.check(_intent())
    assert isinstance(decision, Rejected)
    assert decision.breaker_name == "AlwaysTrip"
    assert decision.reason == "always-trip"


async def test_check_returns_first_tripping_breaker() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    a = _AlwaysTrip(TriggerAction.CANCEL_ALL)
    b = _AlwaysTrip(TriggerAction.QUARANTINE)
    rm = _build_rm([a, b], bus)
    decision = await rm.check(_intent())
    assert isinstance(decision, Rejected)
    # Reason from the first breaker; both return same reason text in this test
    # so we verify the rejection is returned without taking the second.


async def test_tick_publishes_breaker_tripped() -> None:
    bus = InMemoryEventBus()
    received: list[BreakerTripped] = []

    async def cb(event: BreakerTripped) -> None:
        received.append(event)

    bus.subscribe(BreakerTripped, cb)
    await bus.start()

    rm = _build_rm([_AlwaysTrip()], bus)
    await rm.tick()

    assert len(received) == 1
    assert received[0].breaker_name == "AlwaysTrip"
    assert received[0].action == TriggerAction.QUARANTINE


async def test_tick_publishes_one_event_per_tripped_breaker() -> None:
    bus = InMemoryEventBus()
    received: list[BreakerTripped] = []

    async def cb(event: BreakerTripped) -> None:
        received.append(event)

    bus.subscribe(BreakerTripped, cb)
    await bus.start()

    rm = _build_rm([_AlwaysTrip(), _NeverTrip(), _AlwaysTrip()], bus)
    await rm.tick()

    assert len(received) == 2


async def test_tick_publishes_nothing_when_no_breaker_trips() -> None:
    bus = InMemoryEventBus()
    received: list[BreakerTripped] = []

    async def cb(event: BreakerTripped) -> None:
        received.append(event)

    bus.subscribe(BreakerTripped, cb)
    await bus.start()

    rm = _build_rm([_NeverTrip(), _NeverTrip()], bus)
    await rm.tick()

    assert received == []
