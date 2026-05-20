"""ConsecutiveErrorBreaker contract tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.domain import Balance, RiskContext, TriggerAction
from trading.risk.breakers import ConsecutiveErrorBreaker
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.events import ErrorOccurred


def _ctx(now: datetime) -> RiskContext:
    bal = Balance("USDT", Decimal("0"), Decimal("0"), Decimal("0"), now)
    return RiskContext(
        now=now,
        positions=(),
        balance=bal,
        open_orders=(),
        funding_rates={},
    )


def _error(component: str, ts: datetime) -> ErrorOccurred:
    return ErrorOccurred(
        component=component,
        message="boom",
        exception_type="RuntimeError",
        timestamp=ts,
    )


async def test_no_errors_does_not_trip() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=3, window=timedelta(seconds=1))
    assert breaker.evaluate(_ctx(datetime.now(timezone.utc))) is None


async def test_below_threshold_does_not_trip() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=3, window=timedelta(seconds=1))
    now = datetime.now(timezone.utc)
    await bus.publish(_error("X", now))
    await bus.publish(_error("X", now))
    assert breaker.evaluate(_ctx(now)) is None


async def test_at_threshold_trips() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=3, window=timedelta(seconds=1))
    now = datetime.now(timezone.utc)
    for _ in range(3):
        await bus.publish(_error("X", now))

    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
    assert trigger.action == TriggerAction.QUARANTINE
    assert "3 errors" in trigger.reason


async def test_above_threshold_trips() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=3, window=timedelta(seconds=1))
    now = datetime.now(timezone.utc)
    for _ in range(5):
        await bus.publish(_error("X", now))

    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
    assert "5 errors" in trigger.reason


async def test_errors_outside_window_are_pruned() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=2, window=timedelta(milliseconds=100))
    now = datetime.now(timezone.utc)
    for _ in range(5):
        await bus.publish(_error("X", now - timedelta(seconds=10)))

    assert breaker.evaluate(_ctx(now)) is None


async def test_mixed_in_window_and_out_of_window_counts_only_recent() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=3, window=timedelta(milliseconds=100))
    now = datetime.now(timezone.utc)
    # 5 old (outside window) + 2 recent
    for _ in range(5):
        await bus.publish(_error("X", now - timedelta(seconds=10)))
    for _ in range(2):
        await bus.publish(_error("X", now))

    # Only 2 recent < threshold 3 → no trip
    assert breaker.evaluate(_ctx(now)) is None


async def test_component_filter_includes_matching() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(
        bus=bus,
        threshold=2,
        window=timedelta(seconds=1),
        components=["A", "B"],
    )
    now = datetime.now(timezone.utc)
    await bus.publish(_error("A", now))
    await bus.publish(_error("B", now))

    assert breaker.evaluate(_ctx(now)) is not None


async def test_component_filter_excludes_non_matching() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(
        bus=bus,
        threshold=2,
        window=timedelta(seconds=1),
        components=["A"],
    )
    now = datetime.now(timezone.utc)
    await bus.publish(_error("B", now))
    await bus.publish(_error("C", now))

    assert breaker.evaluate(_ctx(now)) is None


async def test_no_component_filter_counts_all_sources() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=2, window=timedelta(seconds=1))
    now = datetime.now(timezone.utc)
    await bus.publish(_error("A", now))
    await bus.publish(_error("Z", now))

    assert breaker.evaluate(_ctx(now)) is not None


async def test_breaker_is_non_latching() -> None:
    """Trips while errors are in window, clears when they age out."""
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(bus=bus, threshold=2, window=timedelta(milliseconds=100))
    base = datetime.now(timezone.utc)
    await bus.publish(_error("X", base))
    await bus.publish(_error("X", base))

    assert breaker.evaluate(_ctx(base)) is not None

    later = base + timedelta(milliseconds=200)
    assert breaker.evaluate(_ctx(later)) is None


async def test_threshold_must_be_positive() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    with pytest.raises(ValueError, match="threshold"):
        ConsecutiveErrorBreaker(bus=bus, threshold=0, window=timedelta(seconds=1))


async def test_custom_action_propagates() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = ConsecutiveErrorBreaker(
        bus=bus,
        threshold=1,
        window=timedelta(seconds=1),
        action=TriggerAction.CANCEL_ALL,
    )
    now = datetime.now(timezone.utc)
    await bus.publish(_error("X", now))

    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
    assert trigger.action == TriggerAction.CANCEL_ALL
