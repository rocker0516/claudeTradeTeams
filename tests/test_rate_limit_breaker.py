"""RateLimitBreaker contract tests.

Stateful breaker with auto-clearing cooldown semantic.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.domain import Balance, RiskContext, TriggerAction
from trading.risk.breakers import RateLimitBreaker
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.events import ErrorOccurred


def _ctx(now: datetime) -> RiskContext:
    bal = Balance("USDT", Decimal("1000"), Decimal("1000"), Decimal("0"), now)
    return RiskContext(now=now, positions=(), balance=bal, open_orders=(), funding_rates={})


def _err(
    exception_type: str = "RateLimitError",
    ts: datetime | None = None,
) -> ErrorOccurred:
    return ErrorOccurred(
        component="Venue",
        message="429",
        exception_type=exception_type,
        timestamp=ts or datetime.now(timezone.utc),
    )


async def test_no_errors_does_not_trip() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=3,
        window=timedelta(seconds=10),
        cooldown=timedelta(minutes=1),
    )
    assert breaker.evaluate(_ctx(datetime.now(timezone.utc))) is None


async def test_below_threshold_does_not_trip() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=3,
        window=timedelta(seconds=10),
        cooldown=timedelta(minutes=1),
    )
    now = datetime.now(timezone.utc)
    await bus.publish(_err(ts=now))
    await bus.publish(_err(ts=now))
    assert breaker.evaluate(_ctx(now)) is None


async def test_at_threshold_trips_and_enters_cooldown() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=3,
        window=timedelta(seconds=10),
        cooldown=timedelta(minutes=5),
    )
    now = datetime.now(timezone.utc)
    for _ in range(3):
        await bus.publish(_err(ts=now))

    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
    assert trigger.action == TriggerAction.REJECT_NEW
    assert "rate-limit" in trigger.reason.lower()


async def test_exception_type_filter_excludes_non_matching() -> None:
    """Errors that don't match the type filter are ignored."""
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=2,
        window=timedelta(seconds=10),
        cooldown=timedelta(minutes=1),
        exception_type_match="RateLimit",
    )
    now = datetime.now(timezone.utc)
    # ConnectionError doesn't match "RateLimit"
    await bus.publish(_err(exception_type="ConnectionError", ts=now))
    await bus.publish(_err(exception_type="ConnectionError", ts=now))
    assert breaker.evaluate(_ctx(now)) is None


async def test_cooldown_keeps_tripping_during_window() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=2,
        window=timedelta(seconds=10),
        cooldown=timedelta(seconds=60),
    )
    base = datetime.now(timezone.utc)
    await bus.publish(_err(ts=base))
    await bus.publish(_err(ts=base))
    # First eval trips and starts cooldown
    first = breaker.evaluate(_ctx(base))
    assert first is not None

    # 30 seconds later — still in cooldown
    later = base + timedelta(seconds=30)
    second = breaker.evaluate(_ctx(later))
    assert second is not None
    assert "cooldown" in second.reason.lower()


async def test_cooldown_clears_after_expiry() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=2,
        window=timedelta(seconds=10),
        cooldown=timedelta(seconds=30),
    )
    base = datetime.now(timezone.utc)
    await bus.publish(_err(ts=base))
    await bus.publish(_err(ts=base))
    breaker.evaluate(_ctx(base))  # trips, cooldown until base+30s

    # Past cooldown — should clear
    later = base + timedelta(seconds=60)
    assert breaker.evaluate(_ctx(later)) is None


async def test_errors_outside_window_pruned() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=3,
        window=timedelta(seconds=10),
        cooldown=timedelta(minutes=1),
    )
    now = datetime.now(timezone.utc)
    # 5 errors well outside window
    for _ in range(5):
        await bus.publish(_err(ts=now - timedelta(seconds=60)))

    assert breaker.evaluate(_ctx(now)) is None


async def test_threshold_must_be_positive() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    with pytest.raises(ValueError, match="threshold"):
        RateLimitBreaker(
            bus=bus,
            threshold=0,
            window=timedelta(seconds=10),
            cooldown=timedelta(minutes=1),
        )


async def test_custom_match_string() -> None:
    """The match is a substring of exception_type — works with any client library."""
    bus = InMemoryEventBus()
    await bus.start()
    breaker = RateLimitBreaker(
        bus=bus,
        threshold=1,
        window=timedelta(seconds=10),
        cooldown=timedelta(minutes=1),
        exception_type_match="429",
    )
    now = datetime.now(timezone.utc)
    await bus.publish(_err(exception_type="HTTP429Error", ts=now))
    trigger = breaker.evaluate(_ctx(now))
    assert trigger is not None
