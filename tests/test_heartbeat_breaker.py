"""HeartbeatBreaker contract tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.domain import Balance, RiskContext, TriggerAction
from trading.risk.breakers import HeartbeatBreaker
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.events import Heartbeat


def _ctx(now: datetime) -> RiskContext:
    bal = Balance("USDT", Decimal("0"), Decimal("0"), Decimal("0"), now)
    return RiskContext(
        now=now,
        positions=(),
        balance=bal,
        open_orders=(),
        funding_rates={},
    )


async def test_never_seen_component_does_not_trip() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = HeartbeatBreaker(
        bus=bus,
        components=["X"],
        threshold=timedelta(milliseconds=10),
    )
    assert breaker.evaluate(_ctx(datetime.now(timezone.utc))) is None


async def test_recent_heartbeat_does_not_trip() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = HeartbeatBreaker(
        bus=bus,
        components=["X"],
        threshold=timedelta(seconds=1),
    )
    now = datetime.now(timezone.utc)
    await bus.publish(Heartbeat(component="X", timestamp=now))
    assert breaker.evaluate(_ctx(now)) is None


async def test_stale_heartbeat_trips() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = HeartbeatBreaker(
        bus=bus,
        components=["X"],
        threshold=timedelta(milliseconds=50),
    )
    now = datetime.now(timezone.utc)
    await bus.publish(Heartbeat(component="X", timestamp=now - timedelta(milliseconds=100)))

    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
    assert "X" in trigger.reason
    assert trigger.action == TriggerAction.QUARANTINE


async def test_breaker_is_non_latching() -> None:
    """Trips when stale, clears on fresh heartbeat."""
    bus = InMemoryEventBus()
    await bus.start()
    breaker = HeartbeatBreaker(
        bus=bus,
        components=["X"],
        threshold=timedelta(milliseconds=50),
    )
    now = datetime.now(timezone.utc)
    await bus.publish(Heartbeat(component="X", timestamp=now - timedelta(milliseconds=100)))
    assert breaker.evaluate(_ctx(now)) is not None

    await bus.publish(Heartbeat(component="X", timestamp=now))
    assert breaker.evaluate(_ctx(now)) is None


async def test_first_stale_component_trips_when_others_fresh() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = HeartbeatBreaker(
        bus=bus,
        components=["A", "B"],
        threshold=timedelta(milliseconds=50),
    )
    now = datetime.now(timezone.utc)
    await bus.publish(Heartbeat(component="A", timestamp=now))
    await bus.publish(Heartbeat(component="B", timestamp=now - timedelta(milliseconds=200)))

    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
    assert "B" in trigger.reason


async def test_custom_action_propagates_to_trigger() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = HeartbeatBreaker(
        bus=bus,
        components=["X"],
        threshold=timedelta(milliseconds=10),
        action=TriggerAction.CANCEL_ALL,
    )
    now = datetime.now(timezone.utc)
    await bus.publish(Heartbeat(component="X", timestamp=now - timedelta(seconds=1)))

    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
    assert trigger.action == TriggerAction.CANCEL_ALL


async def test_ignores_heartbeats_for_unwatched_components() -> None:
    """Heartbeat from a component we don't watch doesn't affect state."""
    bus = InMemoryEventBus()
    await bus.start()
    breaker = HeartbeatBreaker(
        bus=bus,
        components=["X"],
        threshold=timedelta(milliseconds=50),
    )
    now = datetime.now(timezone.utc)
    # Publish for an unrelated component
    await bus.publish(Heartbeat(component="Other", timestamp=now))
    # X still never seen → no trip
    assert breaker.evaluate(_ctx(now)) is None
