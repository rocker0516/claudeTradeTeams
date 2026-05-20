"""Bybit V5 order-semantic mappings — used only by the Venue.

Pure wire decoders (numbers / timestamps / side / position-side) live in
trading.bybit.wire, shared with the public market-data source. This module
holds the order-domain mappings the MarketDataSource doesn't need:
orderType / timeInForce / orderStatus / liqPrice / execType.

Unmapped statuses fail loud (Phase 0 places no conditional orders). Enum
string spellings come from the Bybit V5 docs; re-verify against a real
order response before sign-off (docs/BYBIT_ABC_ALIGNMENT.md §5).
"""

from __future__ import annotations

from decimal import Decimal

from ...domain import OrderStatus, OrderType, TimeInForce

# ============================================================
# Liquidation price ("" or "0" -> None)
# ============================================================


def to_liquidation_price(value: str) -> Decimal | None:
    """Bybit liqPrice is "" or "0" when there is no liquidation price
    (cross-margin, no position, or fully hedged)."""
    if value in ("", "0"):
        return None
    return Decimal(value)


# ============================================================
# Order type (Phase 0: Market / Limit only)
# ============================================================

_ORDER_TYPE_TO_BYBIT: dict[OrderType, str] = {
    OrderType.MARKET: "Market",
    OrderType.LIMIT: "Limit",
}
_ORDER_TYPE_FROM_BYBIT: dict[str, OrderType] = {v: k for k, v in _ORDER_TYPE_TO_BYBIT.items()}


def order_type_to_bybit(order_type: OrderType) -> str:
    try:
        return _ORDER_TYPE_TO_BYBIT[order_type]
    except KeyError:
        raise ValueError(
            f"order type {order_type.value!r} not supported by BybitVenue in Phase 0 "
            "(conditional / stop orders excluded)"
        ) from None


def order_type_from_bybit(value: str) -> OrderType:
    try:
        return _ORDER_TYPE_FROM_BYBIT[value]
    except KeyError:
        raise ValueError(f"unmapped Bybit orderType: {value!r}") from None


# ============================================================
# Time in force (note: POST_ONLY -> "PostOnly", doc §2.1)
# ============================================================

_TIF_TO_BYBIT: dict[TimeInForce, str] = {
    TimeInForce.GTC: "GTC",
    TimeInForce.IOC: "IOC",
    TimeInForce.FOK: "FOK",
    TimeInForce.POST_ONLY: "PostOnly",
}
_TIF_FROM_BYBIT: dict[str, TimeInForce] = {v: k for k, v in _TIF_TO_BYBIT.items()}


def time_in_force_to_bybit(tif: TimeInForce) -> str:
    return _TIF_TO_BYBIT[tif]


def time_in_force_from_bybit(value: str) -> TimeInForce:
    try:
        return _TIF_FROM_BYBIT[value]
    except KeyError:
        raise ValueError(f"unmapped Bybit timeInForce: {value!r}") from None


# ============================================================
# Order status (11 Bybit values -> 8 of ours; unmapped fail loud)
# ============================================================

_STATUS_FROM_BYBIT: dict[str, OrderStatus] = {
    "Created": OrderStatus.SUBMITTED,
    "New": OrderStatus.ACKED,
    "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELLED,
    # cumExecQty > 0 here — venue must still route the residual fill notification
    "PartiallyFilledCanceled": OrderStatus.CANCELLED,
    "Rejected": OrderStatus.REJECTED,
}


def order_status_from_bybit(value: str) -> OrderStatus:
    """Map a Bybit orderStatus to our OrderStatus.

    Unknown / unmapped statuses (Untriggered / Triggered / Deactivated /
    Active — all conditional or TP-SL orders) raise. Phase 0 never places
    such orders, so receiving one means an assumption broke: the venue
    layer is expected to fail loud (log + alert) rather than silently
    coercing it to some default.
    """
    try:
        return _STATUS_FROM_BYBIT[value]
    except KeyError:
        raise ValueError(
            f"unmapped Bybit orderStatus: {value!r} "
            "(conditional / TP-SL orders are not supported in Phase 0)"
        ) from None


# ============================================================
# Execution type filter
# ============================================================


def is_trade_execution(exec_type: str) -> bool:
    """Phase 0 only processes real trade fills. Funding / AdlTrade /
    BustTrade / Settle affect PnL but are not order fills — the venue logs
    and skips them (doc §2.4)."""
    return exec_type == "Trade"
