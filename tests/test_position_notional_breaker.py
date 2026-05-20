"""PositionNotionalBreaker contract tests."""

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
from trading.risk.breakers import PositionNotionalBreaker


def _pos(
    *,
    symbol: str = "BTCUSDT",
    quantity: str = "1",
    mark_price: str = "100",
    side: PositionSide = PositionSide.LONG,
) -> Position:
    now = datetime.now(timezone.utc)
    return Position(
        symbol=Symbol(symbol),
        side=side,
        quantity=Decimal(quantity),
        entry_price=Decimal(mark_price),
        mark_price=Decimal(mark_price),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("1"),
        liquidation_price=None,
        updated_at=now,
    )


def _ctx(positions: tuple[Position, ...]) -> RiskContext:
    now = datetime.now(timezone.utc)
    bal = Balance("USDT", Decimal("100000"), Decimal("100000"), Decimal("0"), now)
    return RiskContext(now=now, positions=positions, balance=bal, open_orders=(), funding_rates={})


def test_empty_positions_does_not_trip() -> None:
    breaker = PositionNotionalBreaker(max_notional_usd=Decimal("10000"))
    assert breaker.evaluate(_ctx(())) is None


def test_notional_below_cap_does_not_trip() -> None:
    # 5 * 100 = 500 notional, cap 10000
    breaker = PositionNotionalBreaker(max_notional_usd=Decimal("10000"))
    assert breaker.evaluate(_ctx((_pos(quantity="5", mark_price="100"),))) is None


def test_notional_at_cap_does_not_trip() -> None:
    """Strict >, not >=."""
    # 10 * 1000 = 10000 = cap
    breaker = PositionNotionalBreaker(max_notional_usd=Decimal("10000"))
    assert breaker.evaluate(_ctx((_pos(quantity="10", mark_price="1000"),))) is None


def test_notional_above_cap_trips() -> None:
    # 10 * 1500 = 15000 > 10000
    breaker = PositionNotionalBreaker(max_notional_usd=Decimal("10000"))
    trigger = breaker.evaluate(_ctx((_pos(quantity="10", mark_price="1500"),)))
    assert trigger is not None
    assert trigger.action == TriggerAction.REJECT_NEW
    assert "15000" in trigger.reason


def test_any_position_over_cap_trips() -> None:
    breaker = PositionNotionalBreaker(max_notional_usd=Decimal("10000"))
    positions = (
        _pos(symbol="BTCUSDT", quantity="1", mark_price="50"),
        _pos(symbol="ETHUSDT", quantity="10", mark_price="2000"),  # 20000
    )
    trigger = breaker.evaluate(_ctx(positions))
    assert trigger is not None
    assert "ETHUSDT" in trigger.reason


def test_max_notional_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_notional_usd"):
        PositionNotionalBreaker(max_notional_usd=Decimal("0"))


def test_uses_abs_quantity_for_short_positions() -> None:
    """Short positions report positive quantity in our model — verify."""
    breaker = PositionNotionalBreaker(max_notional_usd=Decimal("5000"))
    short_pos = _pos(
        side=PositionSide.SHORT,
        quantity="10",  # abs value
        mark_price="1000",  # 10 * 1000 = 10000 > 5000
    )
    trigger = breaker.evaluate(_ctx((short_pos,)))
    assert trigger is not None
