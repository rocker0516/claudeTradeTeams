"""DrawdownBreaker contract tests — first latching breaker.

Latching semantic is the key behavior under test:
  - trips on drawdown > threshold
  - stays tripped even when equity recovers
  - clears on SystemStateChanged(QUARANTINE -> RECONCILING) and only that transition
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.domain import Balance, RiskContext, TriggerAction
from trading.risk.breakers import DrawdownBreaker
from trading.runtime import SystemState, SystemStateChanged
from trading.runtime.event_bus import InMemoryEventBus


def _ctx(now: datetime, equity: Decimal) -> RiskContext:
    bal = Balance("USDT", equity, equity, Decimal("0"), now)
    return RiskContext(
        now=now,
        positions=(),
        balance=bal,
        open_orders=(),
        funding_rates={},
    )


async def _build(
    threshold: str = "0.05", window_seconds: int = 60
) -> tuple[DrawdownBreaker, InMemoryEventBus]:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = DrawdownBreaker(
        bus=bus,
        threshold=Decimal(threshold),
        window=timedelta(seconds=window_seconds),
    )
    return breaker, bus


async def test_single_observation_does_not_trip() -> None:
    breaker, _bus = await _build()
    now = datetime.now(timezone.utc)
    # First evaluate: peak == current, drawdown = 0
    assert breaker.evaluate(_ctx(now, Decimal("1000"))) is None


async def test_flat_equity_does_not_trip() -> None:
    breaker, _bus = await _build()
    now = datetime.now(timezone.utc)
    for i in range(5):
        result = breaker.evaluate(_ctx(now + timedelta(seconds=i), Decimal("1000")))
        assert result is None


async def test_drawdown_above_threshold_trips() -> None:
    breaker, _bus = await _build(threshold="0.05")
    now = datetime.now(timezone.utc)
    # Peak at 1000
    breaker.evaluate(_ctx(now, Decimal("1000")))
    # Drop to 940 → drawdown 6% > 5% threshold
    trigger = breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("940")))
    assert trigger is not None
    assert "drawdown" in trigger.reason.lower()
    assert trigger.action == TriggerAction.FLATTEN


async def test_drawdown_below_threshold_does_not_trip() -> None:
    breaker, _bus = await _build(threshold="0.05")
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))
    # Drop to 970 → drawdown 3% < 5%
    assert breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("970"))) is None


async def test_drawdown_at_threshold_does_not_trip() -> None:
    """Strict >, not >=. At exactly threshold, no trip."""
    breaker, _bus = await _build(threshold="0.10")
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))
    # Drop to 900 → drawdown exactly 10%
    assert breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("900"))) is None


async def test_latches_even_when_equity_recovers() -> None:
    """The defining latching test — trips, then recovers, but stays tripped."""
    breaker, _bus = await _build(threshold="0.05")
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))
    # Trip
    trip = breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("900")))
    assert trip is not None

    # Equity recovers — still latched
    latched = breaker.evaluate(_ctx(now + timedelta(seconds=2), Decimal("1200")))
    assert latched is not None
    assert "latched" in latched.reason.lower()


async def test_acknowledge_via_system_state_change_clears_latch() -> None:
    breaker, bus = await _build(threshold="0.05")
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))
    assert breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("900"))) is not None

    # Simulate Supervisor.acknowledge() transition
    await bus.publish(
        SystemStateChanged(
            old_state=SystemState.QUARANTINE,
            new_state=SystemState.RECONCILING,
            reason="human ack",
            timestamp=now + timedelta(seconds=2),
        )
    )

    # Latch cleared, history cleared — fresh start. Equity 900 is the new baseline.
    assert breaker.evaluate(_ctx(now + timedelta(seconds=3), Decimal("900"))) is None


async def test_other_state_transitions_do_not_clear_latch() -> None:
    """Only QUARANTINE -> RECONCILING clears the latch."""
    breaker, bus = await _build(threshold="0.05")
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))
    assert breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("900"))) is not None

    # Various other transitions — none should clear
    for old, new in [
        (SystemState.RUNNING, SystemState.QUARANTINE),
        (SystemState.RUNNING, SystemState.DRAINING),
        (SystemState.BOOTING, SystemState.RECONCILING),
        (SystemState.RECONCILING, SystemState.RUNNING),
        (SystemState.QUARANTINE, SystemState.DRAINING),
    ]:
        await bus.publish(
            SystemStateChanged(
                old_state=old,
                new_state=new,
                reason="x",
                timestamp=now,
            )
        )

    # Still latched
    latched = breaker.evaluate(_ctx(now + timedelta(seconds=2), Decimal("1200")))
    assert latched is not None
    assert "latched" in latched.reason.lower()


async def test_acknowledge_resets_peak_history() -> None:
    """After un-latch, prior peak is forgotten. Fresh start."""
    breaker, bus = await _build(threshold="0.05")
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))  # old peak
    breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("900")))  # trip

    await bus.publish(
        SystemStateChanged(
            old_state=SystemState.QUARANTINE,
            new_state=SystemState.RECONCILING,
            reason="ack",
            timestamp=now + timedelta(seconds=2),
        )
    )

    # New baseline at 900. Drop to 880 → drawdown 2.2% < 5% threshold.
    # If old peak (1000) were still remembered, drawdown would be 12% and trip.
    breaker.evaluate(_ctx(now + timedelta(seconds=3), Decimal("900")))
    assert breaker.evaluate(_ctx(now + timedelta(seconds=4), Decimal("880"))) is None


async def test_old_peaks_age_out_of_window() -> None:
    breaker, _bus = await _build(threshold="0.05", window_seconds=1)
    now = datetime.now(timezone.utc)
    # Very old peak
    breaker.evaluate(_ctx(now, Decimal("2000")))
    # New series starts after window
    later = now + timedelta(seconds=5)
    breaker.evaluate(_ctx(later, Decimal("1000")))
    # Drop to 970 — drawdown 3% from 1000, but only 3% from new peak.
    # Old peak (2000) should be pruned, otherwise this would be 51.5% drawdown.
    assert breaker.evaluate(_ctx(later + timedelta(milliseconds=100), Decimal("970"))) is None


async def test_default_action_is_flatten() -> None:
    """Drawdown means positions themselves are unsafe — default FLATTEN, not QUARANTINE."""
    breaker, _bus = await _build(threshold="0.01")
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))
    trigger = breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("900")))
    assert trigger is not None
    assert trigger.action == TriggerAction.FLATTEN


async def test_custom_action_propagates() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    breaker = DrawdownBreaker(
        bus=bus,
        threshold=Decimal("0.01"),
        window=timedelta(seconds=60),
        action=TriggerAction.QUARANTINE,
    )
    now = datetime.now(timezone.utc)
    breaker.evaluate(_ctx(now, Decimal("1000")))
    trigger = breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("900")))
    assert trigger is not None
    assert trigger.action == TriggerAction.QUARANTINE


async def test_threshold_must_be_positive() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    with pytest.raises(ValueError, match="threshold"):
        DrawdownBreaker(
            bus=bus,
            threshold=Decimal("0"),
            window=timedelta(seconds=60),
        )
    with pytest.raises(ValueError, match="threshold"):
        DrawdownBreaker(
            bus=bus,
            threshold=Decimal("-0.05"),
            window=timedelta(seconds=60),
        )


async def test_zero_or_negative_peak_does_not_trip() -> None:
    """Drawdown is undefined when peak <= 0. Don't trip — let other guards catch it."""
    breaker, _bus = await _build(threshold="0.05")
    now = datetime.now(timezone.utc)
    # Account at zero
    assert breaker.evaluate(_ctx(now, Decimal("0"))) is None
    # Negative balance — weird but shouldn't crash
    assert breaker.evaluate(_ctx(now + timedelta(seconds=1), Decimal("-100"))) is None
