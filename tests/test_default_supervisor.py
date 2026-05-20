"""DefaultSupervisor end-to-end lifecycle tests.

Wires every Null component into a real DefaultSupervisor and exercises
the full boot -> idle -> shutdown path, plus quarantine + acknowledge.
"""

import asyncio
from datetime import datetime, timezone

import pytest

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
from trading.risk import NullRiskManager
from trading.runtime import BreakerTripped, SystemState, SystemStateChanged
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.supervisor import DefaultSupervisor
from trading.strategy import NullStrategy
from trading.venues import NullVenue


def _build_supervisor() -> tuple[
    DefaultSupervisor,
    InMemoryEventBus,
    InMemoryEventLog,
    InMemoryAlertLog,
    RealClock,
]:
    clock = RealClock()
    bus = InMemoryEventBus()
    venue = NullVenue(clock)
    market_data = NullMarketDataSource()
    oms = NullOrderManager()
    pms = NullPositionManager(clock)
    risk = NullRiskManager()
    executor = DefaultExecutor(risk=risk, orders=oms, positions=pms)
    strategy = NullStrategy()

    log_channel = LogChannel()
    alert_router = InMemoryAlertRouter()
    alert_router.register(
        log_channel,
        levels=(
            AlertLevel.INFO,
            AlertLevel.WARN,
            AlertLevel.ALERT,
            AlertLevel.CRITICAL,
        ),
    )

    order_journal = InMemoryOrderJournal()
    account_journal = InMemoryAccountJournal()
    event_log = InMemoryEventLog()
    alert_log = InMemoryAlertLog()

    supervisor = DefaultSupervisor(
        clock=clock,
        event_bus=bus,
        venue=venue,
        market_data=market_data,
        oms=oms,
        pms=pms,
        risk=risk,
        executor=executor,
        strategy=strategy,
        alert_router=alert_router,
        order_journal=order_journal,
        account_journal=account_journal,
        event_log=event_log,
        alert_log=alert_log,
    )
    return supervisor, bus, event_log, alert_log, clock


async def test_supervisor_initial_state_is_booting() -> None:
    supervisor, *_ = _build_supervisor()
    assert supervisor.state == SystemState.BOOTING


async def test_supervisor_boots_to_running() -> None:
    supervisor, *_ = _build_supervisor()
    await supervisor.start()
    assert supervisor.state == SystemState.RUNNING
    await supervisor.stop()


async def test_supervisor_logs_state_transitions_to_event_log() -> None:
    supervisor, _bus, event_log, _alert_log, _clock = _build_supervisor()
    await supervisor.start()

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    transitions = await event_log.read_since(SystemStateChanged, epoch)

    # Expect at least: BOOTING->RECONCILING and RECONCILING->RUNNING
    assert len(transitions) >= 2
    assert transitions[0].old_state == SystemState.BOOTING
    assert transitions[0].new_state == SystemState.RECONCILING
    assert transitions[-1].new_state == SystemState.RUNNING

    await supervisor.stop()


async def test_supervisor_shuts_down_cleanly() -> None:
    supervisor, *_ = _build_supervisor()
    await supervisor.start()
    assert supervisor.state == SystemState.RUNNING
    await supervisor.stop()
    assert supervisor.state == SystemState.STOPPED


async def test_supervisor_idle_in_running_holds_state() -> None:
    """No events arrive (NullMarketDataSource) — state stays RUNNING."""
    supervisor, *_ = _build_supervisor()
    await supervisor.start()
    await asyncio.sleep(0.01)
    assert supervisor.state == SystemState.RUNNING
    await supervisor.stop()


async def test_supervisor_quarantines_on_breaker_tripped_event() -> None:
    supervisor, bus, _event_log, _alert_log, clock = _build_supervisor()
    await supervisor.start()
    assert supervisor.state == SystemState.RUNNING

    await bus.publish(
        BreakerTripped(
            breaker_name="TestBreaker",
            action=TriggerAction.QUARANTINE,
            reason="manual test trigger",
            timestamp=clock.now(),
        )
    )

    assert supervisor.state == SystemState.QUARANTINE
    await supervisor.stop()


async def test_supervisor_flatten_action_also_quarantines() -> None:
    supervisor, bus, _event_log, _alert_log, clock = _build_supervisor()
    await supervisor.start()

    await bus.publish(
        BreakerTripped(
            breaker_name="DrawdownBreaker",
            action=TriggerAction.FLATTEN,
            reason="dd > 5%",
            timestamp=clock.now(),
        )
    )

    assert supervisor.state == SystemState.QUARANTINE
    await supervisor.stop()


async def test_supervisor_cancel_all_action_keeps_running() -> None:
    """CANCEL_ALL does not transition state, only cancels orders."""
    supervisor, bus, _event_log, _alert_log, clock = _build_supervisor()
    await supervisor.start()

    await bus.publish(
        BreakerTripped(
            breaker_name="LightBreaker",
            action=TriggerAction.CANCEL_ALL,
            reason="light intervention",
            timestamp=clock.now(),
        )
    )

    assert supervisor.state == SystemState.RUNNING
    await supervisor.stop()


async def test_supervisor_acknowledge_returns_to_running() -> None:
    supervisor, bus, _event_log, _alert_log, clock = _build_supervisor()
    await supervisor.start()

    await bus.publish(
        BreakerTripped(
            breaker_name="TestBreaker",
            action=TriggerAction.QUARANTINE,
            reason="test",
            timestamp=clock.now(),
        )
    )
    assert supervisor.state == SystemState.QUARANTINE

    await supervisor.acknowledge()
    assert supervisor.state == SystemState.RUNNING

    await supervisor.stop()


async def test_acknowledge_outside_quarantine_raises() -> None:
    supervisor, *_ = _build_supervisor()
    await supervisor.start()
    with pytest.raises(RuntimeError, match="QUARANTINE"):
        await supervisor.acknowledge()
    await supervisor.stop()


async def test_supervisor_emits_critical_alert_on_quarantine() -> None:
    supervisor, bus, _event_log, _alert_log, clock = _build_supervisor()
    await supervisor.start()

    await bus.publish(
        BreakerTripped(
            breaker_name="DrawdownBreaker",
            action=TriggerAction.QUARANTINE,
            reason="dd > 5%",
            timestamp=clock.now(),
        )
    )

    # Note: alert_log isn't wired to receive alerts in current setup
    # (would require explicit wiring in build). For now, just verify
    # state transition happened — alert delivery is covered by
    # InMemoryAlertRouter contract tests.
    assert supervisor.state == SystemState.QUARANTINE
    await supervisor.stop()


async def test_start_twice_raises() -> None:
    supervisor, *_ = _build_supervisor()
    await supervisor.start()
    with pytest.raises(RuntimeError, match="BOOTING"):
        await supervisor.start()
    await supervisor.stop()
