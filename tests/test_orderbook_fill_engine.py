"""Tests for OrderbookFillEngine — fees + slippage + partial fills.

Contrast with ImmediateFillEngine (zero fee, zero slippage, fills whole
remaining at top-of-book). These pin the realism that makes paper an
honest live proxy.
"""

from datetime import datetime, timezone
from decimal import Decimal

from trading.domain import (
    ClientOrderId,
    Level,
    Order,
    OrderbookSnapshot,
    OrderId,
    OrderSide,
    OrderStatus,
    OrderType,
    Symbol,
    TimeInForce,
)
from trading.fill import OrderbookFillEngine

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
BTC = Symbol("BTCUSDT")


def _order(
    *,
    side: OrderSide,
    otype: OrderType,
    qty: str,
    price: str | None = None,
    filled: str = "0",
) -> Order:
    return Order(
        order_id=OrderId("o-1"),
        client_order_id=ClientOrderId("c-1"),
        symbol=BTC,
        side=side,
        type=otype,
        quantity=Decimal(qty),
        filled_quantity=Decimal(filled),
        price=Decimal(price) if price is not None else None,
        average_fill_price=None,
        status=OrderStatus.ACKED,
        time_in_force=TimeInForce.GTC,
        reduce_only=False,
        created_at=_TS,
        updated_at=_TS,
    )


def _book(
    *, bids: list[tuple[str, str]] | None = None, asks: list[tuple[str, str]] | None = None
) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        symbol=BTC,
        bids=tuple(Level(Decimal(p), Decimal(q)) for p, q in (bids or [])),
        asks=tuple(Level(Decimal(p), Decimal(q)) for p, q in (asks or [])),
        sequence=1,
        timestamp=_TS,
    )


# ============================================================
# Market orders: fee + slippage + partial
# ============================================================


def test_market_buy_single_level_charges_taker_fee() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="2")
    fill = engine.try_fill(order, _book(asks=[("101", "10")]), _TS)
    assert fill is not None
    assert fill.price == Decimal("101")
    assert fill.quantity == Decimal("2")
    assert fill.is_maker is False
    # taker fee = 101 * 2 * 0.00055
    assert fill.fee == Decimal("101") * Decimal("2") * Decimal("0.00055")


def test_market_buy_walks_book_with_slippage() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="3")
    # consumes 1@101 then 2@102 -> weighted avg (101 + 204)/3
    fill = engine.try_fill(order, _book(asks=[("101", "1"), ("102", "5")]), _TS)
    assert fill is not None
    assert fill.quantity == Decimal("3")
    assert fill.price == Decimal("305") / Decimal("3")
    assert fill.price > Decimal("101")  # slippage above best ask


def test_market_buy_partial_when_book_too_shallow() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="5")
    fill = engine.try_fill(order, _book(asks=[("101", "1")]), _TS)
    assert fill is not None
    assert fill.quantity == Decimal("1")  # partial — only 1 available
    assert fill.price == Decimal("101")


def test_market_sell_walks_bids() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.SELL, otype=OrderType.MARKET, qty="3")
    fill = engine.try_fill(order, _book(bids=[("100", "1"), ("99", "5")]), _TS)
    assert fill is not None
    assert fill.price == Decimal("298") / Decimal("3")  # (100 + 198)/3
    assert fill.is_maker is False


def test_market_buy_empty_book_returns_none() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="1")
    assert engine.try_fill(order, _book(asks=[]), _TS) is None


# ============================================================
# Limit orders: fill at own price (maker)
# ============================================================


def test_limit_buy_not_crossed_returns_none() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.LIMIT, qty="2", price="105")
    assert engine.try_fill(order, _book(asks=[("110", "10")]), _TS) is None


def test_limit_buy_crossed_fills_at_own_price_as_maker() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.LIMIT, qty="2", price="105")
    # ask 104 <= limit 105 → crossed; but maker fills at OWN price 105, not 104
    fill = engine.try_fill(order, _book(asks=[("104", "10")]), _TS)
    assert fill is not None
    assert fill.price == Decimal("105")  # own limit, NOT 104 (that'd be optimistic)
    assert fill.quantity == Decimal("2")
    assert fill.is_maker is True
    assert fill.fee == Decimal("105") * Decimal("2") * Decimal("0.0002")  # maker fee


def test_limit_sell_crossed_fills_at_own_price() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.SELL, otype=OrderType.LIMIT, qty="2", price="105")
    fill = engine.try_fill(order, _book(bids=[("106", "10")]), _TS)
    assert fill is not None
    assert fill.price == Decimal("105")
    assert fill.is_maker is True


# ============================================================
# Guards
# ============================================================


def test_wrong_symbol_returns_none() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="1")
    other = OrderbookSnapshot(
        symbol=Symbol("ETHUSDT"),
        bids=(),
        asks=(Level(Decimal("101"), Decimal("10")),),
        sequence=1,
        timestamp=_TS,
    )
    assert engine.try_fill(order, other, _TS) is None


def test_already_filled_returns_none() -> None:
    engine = OrderbookFillEngine()
    order = _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="2", filled="2")
    assert engine.try_fill(order, _book(asks=[("101", "10")]), _TS) is None


def test_custom_fee_rates() -> None:
    engine = OrderbookFillEngine(maker_fee=Decimal("0.001"), taker_fee=Decimal("0.002"))
    order = _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="1")
    fill = engine.try_fill(order, _book(asks=[("100", "10")]), _TS)
    assert fill is not None
    assert fill.fee == Decimal("100") * Decimal("1") * Decimal("0.002")  # custom taker
