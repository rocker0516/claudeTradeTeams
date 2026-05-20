"""Unit tests for trading.bybit.wire — pure Bybit wire-format decoders.

Shared by venues/ and market_data/; no network or credentials. Pins the
wire contract (string numbers / "" sentinels / ms-epoch / Buy-Sell sides)
documented in docs/BYBIT_ABC_ALIGNMENT.md + docs/BYBIT_MARKET_DATA_ALIGNMENT.md.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading.bybit import wire
from trading.domain import OrderSide, PositionSide

# ============================================================
# Numbers
# ============================================================


def test_to_decimal_parses_string_number() -> None:
    assert wire.to_decimal("76818.50") == Decimal("76818.50")
    assert wire.to_decimal("0") == Decimal("0")
    assert wire.to_decimal("-12.3") == Decimal("-12.3")


def test_to_decimal_empty_string_raises() -> None:
    with pytest.raises(ValueError, match="empty string"):
        wire.to_decimal("")


def test_to_decimal_preserves_precision_no_float() -> None:
    assert wire.to_decimal("0.000000010000001") == Decimal("0.000000010000001")


def test_to_optional_decimal_empty_is_none() -> None:
    assert wire.to_optional_decimal("") is None
    assert wire.to_optional_decimal("123.4") == Decimal("123.4")
    assert wire.to_optional_decimal("0") == Decimal("0")


# ============================================================
# Time
# ============================================================


def test_to_datetime_epoch_zero() -> None:
    assert wire.to_datetime("0") == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_to_datetime_is_utc_aware() -> None:
    dt = wire.to_datetime("1779264000000")
    assert dt.tzinfo == timezone.utc
    assert int(dt.timestamp() * 1000) == 1779264000000


def test_to_datetime_accepts_int() -> None:
    # WS market-data `ts` arrives as an int, not a string (alignment §1.3).
    from_int = wire.to_datetime(1779264000000)
    from_str = wire.to_datetime("1779264000000")
    assert from_int == from_str
    assert from_int.tzinfo == timezone.utc


# ============================================================
# Order side
# ============================================================


def test_side_to_bybit() -> None:
    assert wire.side_to_bybit(OrderSide.BUY) == "Buy"
    assert wire.side_to_bybit(OrderSide.SELL) == "Sell"


def test_side_from_bybit() -> None:
    assert wire.side_from_bybit("Buy") == OrderSide.BUY
    assert wire.side_from_bybit("Sell") == OrderSide.SELL


def test_side_round_trips() -> None:
    for side in OrderSide:
        assert wire.side_from_bybit(wire.side_to_bybit(side)) == side


def test_side_from_bybit_unknown_raises() -> None:
    # Bybit uses capitalized "Buy"/"Sell" — our lower-case value must NOT pass
    with pytest.raises(ValueError, match="unknown Bybit order side"):
        wire.side_from_bybit("buy")


# ============================================================
# Position side
# ============================================================


def test_position_side_from_bybit() -> None:
    assert wire.position_side_from_bybit("") == PositionSide.FLAT
    assert wire.position_side_from_bybit("Buy") == PositionSide.LONG
    assert wire.position_side_from_bybit("Sell") == PositionSide.SHORT


def test_position_side_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown Bybit position side"):
        wire.position_side_from_bybit("Long")
