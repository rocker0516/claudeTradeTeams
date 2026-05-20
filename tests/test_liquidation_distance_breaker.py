"""LiquidationDistanceBreaker contract tests."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading.domain import (
    Balance,
    Position,
    PositionSide,
    RiskContext,
    Symbol,
    TriggerAction,
)
from trading.risk.breakers import LiquidationDistanceBreaker


def _pos(
    *,
    symbol: str = "BTCUSDT",
    mark_price: str = "100",
    liq_price: str | None = "50",
    side: PositionSide = PositionSide.LONG,
) -> Position:
    now = datetime.now(timezone.utc)
    return Position(
        symbol=Symbol(symbol),
        side=side,
        quantity=Decimal("1"),
        entry_price=Decimal(mark_price),
        mark_price=Decimal(mark_price),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("2"),
        liquidation_price=Decimal(liq_price) if liq_price else None,
        updated_at=now,
    )


def _ctx(positions: tuple[Position, ...]) -> RiskContext:
    now = datetime.now(timezone.utc)
    bal = Balance("USDT", Decimal("1000"), Decimal("1000"), Decimal("0"), now)
    return RiskContext(now=now, positions=positions, balance=bal, open_orders=(), funding_rates={})


def test_empty_positions_does_not_trip() -> None:
    breaker = LiquidationDistanceBreaker(min_distance=Decimal("0.20"))
    assert breaker.evaluate(_ctx(())) is None


def test_safe_distance_does_not_trip() -> None:
    # mark 100, liq 50 → distance 50% > 20% threshold
    breaker = LiquidationDistanceBreaker(min_distance=Decimal("0.20"))
    assert breaker.evaluate(_ctx((_pos(mark_price="100", liq_price="50"),))) is None


def test_close_to_liquidation_trips() -> None:
    # mark 100, liq 90 → distance 10% < 20% threshold
    breaker = LiquidationDistanceBreaker(min_distance=Decimal("0.20"))
    trigger = breaker.evaluate(_ctx((_pos(mark_price="100", liq_price="90"),)))
    assert trigger is not None
    assert trigger.action == TriggerAction.FLATTEN
    assert "10" in trigger.reason  # distance percentage somewhere


def test_no_liquidation_price_skipped() -> None:
    """Cross-margin positions can have liquidation_price=None — skip safely."""
    breaker = LiquidationDistanceBreaker(min_distance=Decimal("0.20"))
    assert breaker.evaluate(_ctx((_pos(liq_price=None),))) is None


def test_zero_mark_price_skipped() -> None:
    """Degenerate market data — don't divide by zero, just skip."""
    breaker = LiquidationDistanceBreaker(min_distance=Decimal("0.20"))
    assert breaker.evaluate(_ctx((_pos(mark_price="0", liq_price="0"),))) is None


def test_short_position_close_to_liquidation_above() -> None:
    """Short: liq price is ABOVE mark. Distance = (liq - mark) / mark."""
    breaker = LiquidationDistanceBreaker(min_distance=Decimal("0.15"))
    # mark 100, liq 110 → distance 10% < 15% threshold
    trigger = breaker.evaluate(
        _ctx((_pos(side=PositionSide.SHORT, mark_price="100", liq_price="110"),))
    )
    assert trigger is not None


def test_any_position_too_close_trips() -> None:
    breaker = LiquidationDistanceBreaker(min_distance=Decimal("0.20"))
    positions = (
        _pos(symbol="BTCUSDT", mark_price="100", liq_price="50"),  # safe
        _pos(symbol="ETHUSDT", mark_price="100", liq_price="95"),  # 5% — danger
    )
    trigger = breaker.evaluate(_ctx(positions))
    assert trigger is not None
    assert "ETHUSDT" in trigger.reason


def test_min_distance_must_be_positive() -> None:
    with pytest.raises(ValueError, match="min_distance"):
        LiquidationDistanceBreaker(min_distance=Decimal("0"))


def test_custom_action_propagates() -> None:
    breaker = LiquidationDistanceBreaker(
        min_distance=Decimal("0.20"), action=TriggerAction.QUARANTINE
    )
    trigger = breaker.evaluate(_ctx((_pos(mark_price="100", liq_price="95"),)))
    assert trigger is not None
    assert trigger.action == TriggerAction.QUARANTINE
