"""Pure Bybit V5 wire-format decoders, shared by venues/ and market_data/.

Depends only on domain, so both the private Venue adapter and the public
MarketDataSource can decode Bybit's wire conventions (string numbers, ""
sentinels, ms-epoch timestamps, "Buy"/"Sell" sides) WITHOUT depending on
each other.

Order-semantic mappings (orderType / timeInForce / orderStatus / execType /
liqPrice) that only the Venue needs live in venues/bybit/adapters.py.

Verified against real testnet + mainnet responses on 2026-05-20 — see
docs/BYBIT_ABC_ALIGNMENT.md and docs/BYBIT_MARKET_DATA_ALIGNMENT.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from ..domain import OrderSide, PositionSide

# ============================================================
# Numbers
# ============================================================


def to_decimal(value: str) -> Decimal:
    """Bybit numeric string -> Decimal. Empty string is a caller error
    (use to_optional_decimal for nullable fields)."""
    if value == "":
        raise ValueError("expected a numeric string, got empty string")
    return Decimal(value)


def to_optional_decimal(value: str) -> Decimal | None:
    """Nullable numeric field: "" -> None (e.g. avgPrice before any fill)."""
    if value == "":
        return None
    return Decimal(value)


# ============================================================
# Time
# ============================================================


def to_datetime(ms: str | int) -> datetime:
    """Millisecond-epoch -> timezone-aware UTC datetime.

    Accepts both str (REST responses) and int (WS market-data `ts`).
    See docs/BYBIT_ABC_ALIGNMENT.md §4.7 + docs/BYBIT_MARKET_DATA_ALIGNMENT.md §1.3.
    """
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


# ============================================================
# Order side ("Buy" / "Sell")
# ============================================================

_SIDE_TO_BYBIT: dict[OrderSide, str] = {OrderSide.BUY: "Buy", OrderSide.SELL: "Sell"}
_SIDE_FROM_BYBIT: dict[str, OrderSide] = {v: k for k, v in _SIDE_TO_BYBIT.items()}


def side_to_bybit(side: OrderSide) -> str:
    return _SIDE_TO_BYBIT[side]


def side_from_bybit(value: str) -> OrderSide:
    try:
        return _SIDE_FROM_BYBIT[value]
    except KeyError:
        raise ValueError(f"unknown Bybit order side: {value!r}") from None


# ============================================================
# Position side ("" == flat)
# ============================================================


def position_side_from_bybit(value: str) -> PositionSide:
    if value == "":
        return PositionSide.FLAT
    if value == "Buy":
        return PositionSide.LONG
    if value == "Sell":
        return PositionSide.SHORT
    raise ValueError(f"unknown Bybit position side: {value!r}")
