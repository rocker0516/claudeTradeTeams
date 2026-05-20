"""Unit tests for venues.bybit.adapters — Bybit order-semantic mappings.

Pure wire decoders are tested in test_bybit_wire.py; this file covers the
order-domain mappings only the Venue uses (orderType / timeInForce /
orderStatus / liqPrice / execType), including the fail-loud paths.
"""

from decimal import Decimal

import pytest

from trading.domain import OrderStatus, OrderType, TimeInForce
from trading.venues.bybit import adapters

# ============================================================
# Liquidation price
# ============================================================


def test_to_liquidation_price_empty_or_zero_is_none() -> None:
    assert adapters.to_liquidation_price("") is None
    assert adapters.to_liquidation_price("0") is None
    assert adapters.to_liquidation_price("65000.5") == Decimal("65000.5")


# ============================================================
# Order type
# ============================================================


def test_order_type_to_bybit() -> None:
    assert adapters.order_type_to_bybit(OrderType.MARKET) == "Market"
    assert adapters.order_type_to_bybit(OrderType.LIMIT) == "Limit"


def test_order_type_to_bybit_conditional_raises() -> None:
    with pytest.raises(ValueError, match="not supported by BybitVenue in Phase 0"):
        adapters.order_type_to_bybit(OrderType.STOP_MARKET)
    with pytest.raises(ValueError, match="not supported by BybitVenue in Phase 0"):
        adapters.order_type_to_bybit(OrderType.STOP_LIMIT)


def test_order_type_round_trips_for_supported_types() -> None:
    for ot in (OrderType.MARKET, OrderType.LIMIT):
        assert adapters.order_type_from_bybit(adapters.order_type_to_bybit(ot)) == ot


def test_order_type_from_bybit_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unmapped Bybit orderType"):
        adapters.order_type_from_bybit("UnknownType")


# ============================================================
# Time in force
# ============================================================


def test_time_in_force_to_bybit_post_only_casing() -> None:
    # Bybit spells it "PostOnly" — getting this wrong silently disables
    # maker-only protection, so pin it explicitly.
    assert adapters.time_in_force_to_bybit(TimeInForce.POST_ONLY) == "PostOnly"
    assert adapters.time_in_force_to_bybit(TimeInForce.GTC) == "GTC"
    assert adapters.time_in_force_to_bybit(TimeInForce.IOC) == "IOC"
    assert adapters.time_in_force_to_bybit(TimeInForce.FOK) == "FOK"


def test_time_in_force_round_trips() -> None:
    for tif in TimeInForce:
        assert adapters.time_in_force_from_bybit(adapters.time_in_force_to_bybit(tif)) == tif


def test_time_in_force_from_bybit_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unmapped Bybit timeInForce"):
        adapters.time_in_force_from_bybit("GoodTillDate")


# ============================================================
# Order status
# ============================================================


def test_order_status_mappings() -> None:
    assert adapters.order_status_from_bybit("Created") == OrderStatus.SUBMITTED
    assert adapters.order_status_from_bybit("New") == OrderStatus.ACKED
    assert adapters.order_status_from_bybit("PartiallyFilled") == OrderStatus.PARTIALLY_FILLED
    assert adapters.order_status_from_bybit("Filled") == OrderStatus.FILLED
    assert adapters.order_status_from_bybit("Cancelled") == OrderStatus.CANCELLED
    assert adapters.order_status_from_bybit("Rejected") == OrderStatus.REJECTED


def test_partially_filled_canceled_maps_to_cancelled() -> None:
    # cumExecQty > 0 in this state; status collapses to CANCELLED but the
    # venue must still route the residual fill (asserted at venue layer).
    assert adapters.order_status_from_bybit("PartiallyFilledCanceled") == OrderStatus.CANCELLED


def test_order_status_conditional_statuses_fail_loud() -> None:
    for unsupported in ("Untriggered", "Triggered", "Deactivated", "Active"):
        with pytest.raises(ValueError, match="unmapped Bybit orderStatus"):
            adapters.order_status_from_bybit(unsupported)


def test_order_status_garbage_fails_loud() -> None:
    with pytest.raises(ValueError, match="unmapped Bybit orderStatus"):
        adapters.order_status_from_bybit("totally-made-up")


# ============================================================
# Execution type filter
# ============================================================


def test_is_trade_execution() -> None:
    assert adapters.is_trade_execution("Trade") is True
    for non_trade in ("Funding", "AdlTrade", "BustTrade", "Settle"):
        assert adapters.is_trade_execution(non_trade) is False
