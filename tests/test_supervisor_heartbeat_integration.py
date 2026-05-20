"""End-to-end test for the event-driven control loop.

Wires: Supervisor tick -> Heartbeat publish + Risk.tick -> HeartbeatBreaker
evaluates -> BreakerTripped published -> Supervisor's subscriber catches
it -> transitions to QUARANTINE.

This is the first test that exercises the full closed loop with real
(non-Null) Risk + breaker logic.
"""

import asyncio
from datetime import timedelta

from trading.alerts.channels import LogChannel
from trading.alerts.router import InMemoryAlertRouter
from trading.clock import RealClock
from trading.domain import AlertLevel, TriggerAction
from trading.executor import DefaultExecutor
from trading.market_data import NullMarketDataSource
from trading.oms import NullOrderManager
from trading.persistence.account_journal import InMemoryAccountJournal
from trading.persistence.alert_log import InMemoryAlertLog
from trading.persistence.event_log import InMemoryEventLog
from trading.persistence.order_journal import InMemoryOrderJournal
from trading.pms import NullPositionManager
from trading.risk import DefaultRiskManager
from trading.risk.breakers import HeartbeatBreaker
from trading.runtime import Heartbeat, SystemState
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.supervisor import DefaultSupervisor
from trading.strategy import NullStrategy
from trading.venues import NullVenue


def _build(
    *,
    watched_components: tuple[str, ...],
    threshold: timedelta,
    tick_interval: float,
) -> tuple[DefaultSupervisor, InMemoryEventBus, RealClock]:
    clock = RealClock()
    bus = InMemoryEventBus()
    venue = NullVenue(clock)
    market_data = NullMarketDataSource()
    oms = NullOrderManager()
    pms = NullPositionManager(clock)

    breaker = HeartbeatBreaker(
        bus=bus,
        components=watched_components,
        threshold=threshold,
        action=TriggerAction.QUARANTINE,
    )
    risk = DefaultRiskManager(clock=clock, bus=bus, oms=oms, pms=pms, breakers=[breaker])
    executor = DefaultExecutor(risk=risk, orders=oms, positions=pms)

    alert_router = InMemoryAlertRouter()
    alert_router.register(
        LogChannel(),
        levels=(
            AlertLevel.INFO,
            AlertLevel.WARN,
            AlertLevel.ALERT,
            AlertLevel.CRITICAL,
        ),
    )

    supervisor = DefaultSupervisor(
        clock=clock,
        event_bus=bus,
        venue=venue,
        market_data=market_data,
        oms=oms,
        pms=pms,
        risk=risk,
        executor=executor,
        strategy=NullStrategy(),
        alert_router=alert_router,
        order_journal=InMemoryOrderJournal(),
        account_journal=InMemoryAccountJournal(),
        event_log=InMemoryEventLog(),
        alert_log=InMemoryAlertLog(),
        risk_tick_interval=tick_interval,
    )
    return supervisor, bus, clock


async def test_supervisor_quarantines_on_heartbeat_loss() -> None:
    """End-to-end: silent component -> breaker trips on tick -> QUARANTINE."""
    supervisor, bus, clock = _build(
        watched_components=("Strategy",),
        threshold=timedelta(milliseconds=80),
        tick_interval=0.04,
    )

    await supervisor.start()

    # Register "Strategy" as alive at t=0 with one heartbeat.
    await bus.publish(Heartbeat(component="Strategy", timestamp=clock.now()))

    # Wait for: threshold (80ms) to expire + at least one tick (40ms) after.
    # Total ~200ms gives a safe margin.
    await asyncio.sleep(0.25)

    assert supervisor.state == SystemState.QUARANTINE
    await supervisor.stop()


async def test_supervisor_stays_running_when_watched_component_alive() -> None:
    """If we keep emitting heartbeats faster than threshold, no trip."""
    supervisor, bus, clock = _build(
        watched_components=("Strategy",),
        threshold=timedelta(milliseconds=200),
        tick_interval=0.04,
    )

    await supervisor.start()

    # Pump heartbeats every 30ms for 150ms total — well within 200ms threshold.
    for _ in range(5):
        await bus.publish(Heartbeat(component="Strategy", timestamp=clock.now()))
        await asyncio.sleep(0.03)

    assert supervisor.state == SystemState.RUNNING
    await supervisor.stop()


async def test_supervisor_publishes_own_heartbeat_each_tick() -> None:
    """The tick loop emits Heartbeat(component='Supervisor') each cycle."""
    supervisor, bus, _clock = _build(
        watched_components=("never-watched",),
        threshold=timedelta(seconds=10),
        tick_interval=0.04,
    )
    received: list[Heartbeat] = []

    async def cb(hb: Heartbeat) -> None:
        if hb.component == "Supervisor":
            received.append(hb)

    bus.subscribe(Heartbeat, cb)

    await supervisor.start()
    await asyncio.sleep(0.15)  # 3+ tick intervals
    await supervisor.stop()

    assert len(received) >= 2
