"""LeverageBreaker contract tests."""

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
from trading.risk.breakers import LeverageBreaker


def _pos(
    *,
    symbol: str = "BTCUSDT",
    leverage: str = "1",
    quantity: str = "1",
) -> Position:
    now = datetime.now(timezone.utc)
    return Position(
        symbol=Symbol(symbol),
        side=PositionSide.LONG,
        quantity=Decimal(quantity),
        entry_price=Decimal("100"),
        mark_price=Decimal("100"),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        leverage=Decimal(leverage),
        liquidation_price=None,
        updated_at=now,
    )


def _ctx(positions: tuple[Position, ...]) -> RiskContext:
    now = datetime.now(timezone.utc)
    bal = Balance("USDT", Decimal("1000"), Decimal("1000"), Decimal("0"), now)
    return RiskContext(now=now, positions=positions, balance=bal, open_orders=(), funding_rates={})


def test_empty_positions_does_not_trip() -> None:
    breaker = LeverageBreaker(max_leverage=Decimal("3"))
    assert breaker.evaluate(_ctx(())) is None


def test_leverage_below_threshold_does_not_trip() -> None:
    breaker = LeverageBreaker(max_leverage=Decimal("3"))
    assert breaker.evaluate(_ctx((_pos(leverage="2.5"),))) is None


def test_leverage_at_exact_threshold_does_not_trip() -> None:
    """Strict >, not >=."""
    breaker = LeverageBreaker(max_leverage=Decimal("3"))
    assert breaker.evaluate(_ctx((_pos(leverage="3"),))) is None


def test_leverage_above_threshold_trips() -> None:
    breaker = LeverageBreaker(max_leverage=Decimal("3"))
    trigger = breaker.evaluate(_ctx((_pos(leverage="5"),)))
    assert trigger is not None
    assert trigger.action == TriggerAction.REJECT_NEW
    assert "5" in trigger.reason


def test_any_position_over_threshold_trips() -> None:
    breaker = LeverageBreaker(max_leverage=Decimal("3"))
    positions = (
        _pos(symbol="BTCUSDT", leverage="2"),
        _pos(symbol="ETHUSDT", leverage="10"),
    )
    trigger = breaker.evaluate(_ctx(positions))
    assert trigger is not None
    assert "ETHUSDT" in trigger.reason


def test_non_latching_clears_when_leverage_drops() -> None:
    breaker = LeverageBreaker(max_leverage=Decimal("3"))
    assert breaker.evaluate(_ctx((_pos(leverage="5"),))) is not None
    # Same breaker instance, lower leverage now → clears
    assert breaker.evaluate(_ctx((_pos(leverage="2"),))) is None


def test_custom_action_propagates() -> None:
    breaker = LeverageBreaker(max_leverage=Decimal("3"), action=TriggerAction.CANCEL_ALL)
    trigger = breaker.evaluate(_ctx((_pos(leverage="5"),)))
    assert trigger is not None
    assert trigger.action == TriggerAction.CANCEL_ALL


def test_max_leverage_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_leverage"):
        LeverageBreaker(max_leverage=Decimal("0"))
    with pytest.raises(ValueError, match="max_leverage"):
        LeverageBreaker(max_leverage=Decimal("-1"))
