"""FundingSpikeBreaker contract tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.domain import (
    Balance,
    FundingRate,
    RiskContext,
    Symbol,
    TriggerAction,
)
from trading.risk.breakers import FundingSpikeBreaker


def _fr(symbol: str = "BTCUSDT", rate: str = "0") -> FundingRate:
    now = datetime.now(timezone.utc)
    return FundingRate(
        symbol=Symbol(symbol),
        rate=Decimal(rate),
        predicted_rate=None,
        next_funding_time=now + timedelta(hours=8),
        timestamp=now,
    )


def _ctx(funding_rates: dict[Symbol, FundingRate]) -> RiskContext:
    now = datetime.now(timezone.utc)
    bal = Balance("USDT", Decimal("1000"), Decimal("1000"), Decimal("0"), now)
    return RiskContext(
        now=now,
        positions=(),
        balance=bal,
        open_orders=(),
        funding_rates=funding_rates,
    )


def test_empty_funding_rates_does_not_trip() -> None:
    """No funding rate data → can't evaluate → don't trip."""
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"))
    assert breaker.evaluate(_ctx({})) is None


def test_normal_funding_rate_does_not_trip() -> None:
    # 0.01% per 8h is typical-low
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"))
    rates = {Symbol("BTCUSDT"): _fr(rate="0.0001")}
    assert breaker.evaluate(_ctx(rates)) is None


def test_funding_at_threshold_does_not_trip() -> None:
    """Strict >, not >=."""
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"))
    rates = {Symbol("BTCUSDT"): _fr(rate="0.005")}
    assert breaker.evaluate(_ctx(rates)) is None


def test_high_positive_funding_trips() -> None:
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"))
    rates = {Symbol("BTCUSDT"): _fr(rate="0.01")}
    trigger = breaker.evaluate(_ctx(rates))
    assert trigger is not None
    assert trigger.action == TriggerAction.REJECT_NEW
    assert "BTCUSDT" in trigger.reason


def test_high_negative_funding_trips() -> None:
    """Absolute value — short side spike also trips."""
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"))
    rates = {Symbol("BTCUSDT"): _fr(rate="-0.01")}
    trigger = breaker.evaluate(_ctx(rates))
    assert trigger is not None


def test_symbol_filter_includes_matching() -> None:
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"), symbols=[Symbol("BTCUSDT")])
    rates = {Symbol("BTCUSDT"): _fr(rate="0.01")}
    assert breaker.evaluate(_ctx(rates)) is not None


def test_symbol_filter_excludes_non_matching() -> None:
    """Funding spike on a symbol we don't track → don't trip."""
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"), symbols=[Symbol("BTCUSDT")])
    rates = {Symbol("ETHUSDT"): _fr(symbol="ETHUSDT", rate="0.05")}
    assert breaker.evaluate(_ctx(rates)) is None


def test_no_filter_checks_all_symbols() -> None:
    breaker = FundingSpikeBreaker(max_abs_rate=Decimal("0.005"))
    rates = {
        Symbol("BTCUSDT"): _fr(rate="0.001"),
        Symbol("ETHUSDT"): _fr(symbol="ETHUSDT", rate="0.05"),
    }
    trigger = breaker.evaluate(_ctx(rates))
    assert trigger is not None
    assert "ETHUSDT" in trigger.reason


def test_max_abs_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_abs_rate"):
        FundingSpikeBreaker(max_abs_rate=Decimal("0"))
