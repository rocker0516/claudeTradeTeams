"""ImmediateFillEngine contract tests."""

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
from trading.fill import ImmediateFillEngine


def _book(
    symbol: str = "BTCUSDT",
    *,
    bids: list[tuple[str, str]] | None = None,
    asks: list[tuple[str, str]] | None = None,
    now: datetime | None = None,
) -> OrderbookSnapshot:
    now = now or datetime.now(timezone.utc)
    # Use `is None` not `or` — empty list `[]` is falsy but valid input
    bids_to_use = bids if bids is not None else [("100", "1")]
    asks_to_use = asks if asks is not None else [("101", "1")]
    b = tuple(Level(Decimal(p), Decimal(q)) for p, q in bids_to_use)
    a = tuple(Level(Decimal(p), Decimal(q)) for p, q in asks_to_use)
    return OrderbookSnapshot(
        symbol=Symbol(symbol),
        bids=b,
        asks=a,
        sequence=1,
        timestamp=now,
    )


def _order(
    *,
    side: OrderSide = OrderSide.BUY,
    type_: OrderType = OrderType.MARKET,
    tif: TimeInForce = TimeInForce.GTC,
    quantity: str = "1",
    filled: str = "0",
    price: str | None = None,
    symbol: str = "BTCUSDT",
) -> Order:
    now = datetime.now(timezone.utc)
    return Order(
        order_id=OrderId("o-1"),
        client_order_id=ClientOrderId("c-1"),
        symbol=Symbol(symbol),
        side=side,
        type=type_,
        quantity=Decimal(quantity),
        filled_quantity=Decimal(filled),
        price=Decimal(price) if price else None,
        average_fill_price=None,
        status=OrderStatus.ACKED,
        time_in_force=tif,
        reduce_only=False,
        created_at=now,
        updated_at=now,
    )


def test_market_buy_fills_at_best_ask() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(side=OrderSide.BUY, type_=OrderType.MARKET),
        _book(asks=[("105", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is not None
    assert fill.price == Decimal("105")
    assert fill.quantity == Decimal("1")
    assert fill.side == OrderSide.BUY


def test_market_sell_fills_at_best_bid() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(side=OrderSide.SELL, type_=OrderType.MARKET),
        _book(bids=[("99", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is not None
    assert fill.price == Decimal("99")


def test_market_buy_with_empty_asks_does_not_fill() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(side=OrderSide.BUY, type_=OrderType.MARKET),
        _book(asks=[]),
        datetime.now(timezone.utc),
    )
    assert fill is None


def test_limit_buy_crosses_fills_at_best_ask() -> None:
    engine = ImmediateFillEngine()
    # Limit 105, best ask 102 → crossed, fill at 102
    fill = engine.try_fill(
        _order(side=OrderSide.BUY, type_=OrderType.LIMIT, price="105"),
        _book(asks=[("102", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is not None
    assert fill.price == Decimal("102")


def test_limit_buy_not_crossed_does_not_fill() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(side=OrderSide.BUY, type_=OrderType.LIMIT, price="95"),
        _book(asks=[("102", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is None


def test_limit_sell_crosses_fills_at_best_bid() -> None:
    engine = ImmediateFillEngine()
    # Limit 95, best bid 100 → bid > limit, crossed, fill at 100
    fill = engine.try_fill(
        _order(side=OrderSide.SELL, type_=OrderType.LIMIT, price="95"),
        _book(bids=[("100", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is not None
    assert fill.price == Decimal("100")


def test_limit_sell_not_crossed_does_not_fill() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(side=OrderSide.SELL, type_=OrderType.LIMIT, price="110"),
        _book(bids=[("100", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is None


def test_post_only_tif_does_not_change_fill_behavior() -> None:
    """POST_ONLY is a TIF, not an OrderType. ImmediateFillEngine treats
    a LIMIT+POST_ONLY order the same as a regular LIMIT order — once
    accepted by the venue, it fills when the book crosses.

    The "reject on would-cross at submission" semantic lives in the venue
    (PaperVenue.place_order), not in this engine.
    """
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(
            side=OrderSide.BUY,
            type_=OrderType.LIMIT,
            tif=TimeInForce.POST_ONLY,
            price="105",
        ),
        _book(asks=[("100", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is not None
    assert fill.price == Decimal("100")


def test_stop_market_not_supported() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(side=OrderSide.BUY, type_=OrderType.STOP_MARKET),
        _book(),
        datetime.now(timezone.utc),
    )
    assert fill is None


def test_fill_quantity_excludes_already_filled() -> None:
    """If order has filled_quantity > 0, only remainder fills."""
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(quantity="10", filled="3"),
        _book(asks=[("100", "100")]),
        datetime.now(timezone.utc),
    )
    assert fill is not None
    assert fill.quantity == Decimal("7")


def test_fully_filled_order_does_not_fill_again() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(quantity="5", filled="5"),
        _book(),
        datetime.now(timezone.utc),
    )
    assert fill is None


def test_wrong_symbol_does_not_fill() -> None:
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(symbol="BTCUSDT"),
        _book(symbol="ETHUSDT"),
        datetime.now(timezone.utc),
    )
    assert fill is None


def test_limit_at_exact_best_ask_crosses() -> None:
    """Limit == best ask should fill (>= semantics)."""
    engine = ImmediateFillEngine()
    fill = engine.try_fill(
        _order(side=OrderSide.BUY, type_=OrderType.LIMIT, price="100"),
        _book(asks=[("100", "1")]),
        datetime.now(timezone.utc),
    )
    assert fill is not None
    assert fill.price == Decimal("100")
