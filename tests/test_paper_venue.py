"""PaperVenue + ImmediateFillEngine integration tests."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal

from trading.clock import RealClock
from trading.domain import (
    ClientOrderId,
    FundingRate,
    Level,
    OrderbookSnapshot,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Symbol,
    TimeInForce,
    Trade,
)
from trading.fill import ImmediateFillEngine, OrderbookFillEngine
from trading.market_data import MarketDataSource
from trading.venues import PaperVenue


class _PushableMarketData(MarketDataSource):
    """MarketDataSource that lets tests fire orderbook events programmatically."""

    def __init__(self) -> None:
        self._orderbook_cbs: dict[Symbol, list[Callable[[OrderbookSnapshot], Awaitable[None]]]] = {}

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def subscribe_orderbook(
        self,
        symbol: Symbol,
        callback: Callable[[OrderbookSnapshot], Awaitable[None]],
    ) -> None:
        self._orderbook_cbs.setdefault(symbol, []).append(callback)

    def subscribe_trades(
        self, symbol: Symbol, callback: Callable[[Trade], Awaitable[None]]
    ) -> None:
        pass

    def subscribe_funding(
        self,
        symbol: Symbol,
        callback: Callable[[FundingRate], Awaitable[None]],
    ) -> None:
        pass

    async def push(self, snapshot: OrderbookSnapshot) -> None:
        for cb in self._orderbook_cbs.get(snapshot.symbol, []):
            await cb(snapshot)


def _book(
    *,
    symbol: str = "BTCUSDT",
    bid: str = "100",
    ask: str = "101",
    seq: int = 1,
) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        symbol=Symbol(symbol),
        bids=(Level(Decimal(bid), Decimal("10")),),
        asks=(Level(Decimal(ask), Decimal("10")),),
        sequence=seq,
        timestamp=datetime.now(timezone.utc),
    )


def _market_request(
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: str = "1",
    symbol: str = "BTCUSDT",
    cid: str = "c-1",
) -> OrderRequest:
    return OrderRequest(
        symbol=Symbol(symbol),
        side=side,
        type=OrderType.MARKET,
        quantity=Decimal(quantity),
        client_order_id=ClientOrderId(cid),
        price=None,
        time_in_force=TimeInForce.IOC,
    )


def _limit_request(
    *,
    side: OrderSide = OrderSide.BUY,
    price: str,
    quantity: str = "1",
    symbol: str = "BTCUSDT",
    cid: str = "c-1",
    tif: TimeInForce = TimeInForce.GTC,
) -> OrderRequest:
    return OrderRequest(
        symbol=Symbol(symbol),
        side=side,
        type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        client_order_id=ClientOrderId(cid),
        price=Decimal(price),
        time_in_force=tif,
    )


def _build(initial_balance: str = "10000") -> tuple[PaperVenue, _PushableMarketData, RealClock]:
    clock = RealClock()
    market_data = _PushableMarketData()
    venue = PaperVenue(
        market_data=market_data,
        fill_engine=ImmediateFillEngine(),
        clock=clock,
        symbols=[Symbol("BTCUSDT"), Symbol("ETHUSDT")],
        initial_balance=Decimal(initial_balance),
    )
    return venue, market_data, clock


# ============================================================
# Fees (OrderbookFillEngine deducts; ImmediateFillEngine = 0)
# ============================================================


async def test_orderbook_fill_engine_fee_reduces_balance() -> None:
    """A fee-charging engine must move realized PnL / balance. Pins that
    PaperVenue actually deducts Fill.fee (ImmediateFillEngine has fee=0, so
    every other test in this file is unaffected)."""
    clock = RealClock()
    market_data = _PushableMarketData()
    venue = PaperVenue(
        market_data=market_data,
        fill_engine=OrderbookFillEngine(taker_fee=Decimal("0.001")),
        clock=clock,
        symbols=[Symbol("BTCUSDT")],
        initial_balance=Decimal("10000"),
    )
    await venue.start()
    # tight book: fill price == mark price, so unrealized is 0 and only the
    # fee moves the balance
    await market_data.push(_book(bid="100", ask="100"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="1"))

    # market buy 1 @ 100, taker fee = 100 * 1 * 0.001 = 0.1
    # realized = -0.1 (fee), unrealized = 0 → balance = 9999.9
    bal = await venue.get_balance()
    assert bal.total == Decimal("9999.9")


# ============================================================
# Order lifecycle
# ============================================================


async def test_place_order_returns_order_id_and_fires_ack() -> None:
    venue, _md, _clock = _build()
    received_updates = []

    async def cb(order):
        received_updates.append(order)

    venue.on_order_update(cb)
    await venue.start()

    oid = await venue.place_order(_limit_request(price="50"))
    assert oid.startswith("paper-")
    # Expect at least one ACKED update
    assert any(u.status == OrderStatus.ACKED for u in received_updates)


async def test_limit_order_stays_open_when_not_crossed() -> None:
    venue, md, _clock = _build()
    await venue.start()
    # Limit buy at 50 with book bid=100 ask=101 → not crossed
    oid = await venue.place_order(_limit_request(price="50"))
    await md.push(_book(bid="100", ask="101"))

    open_orders = await venue.get_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].order_id == oid


async def test_market_buy_fills_immediately_at_best_ask() -> None:
    venue, md, _clock = _build()
    fills = []

    async def cb_fill(f):
        fills.append(f)

    venue.on_fill(cb_fill)
    await venue.start()
    # Prime the book first
    await md.push(_book(bid="100", ask="101"))

    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="2"))

    assert len(fills) == 1
    assert fills[0].price == Decimal("101")
    assert fills[0].quantity == Decimal("2")


async def test_limit_buy_fills_when_orderbook_crosses() -> None:
    venue, md, _clock = _build()
    fills = []

    async def cb_fill(f):
        fills.append(f)

    venue.on_fill(cb_fill)
    await venue.start()
    # Place limit buy at 105, no fill yet (no book)
    await venue.place_order(_limit_request(price="105"))
    assert fills == []

    # Push a book where ask=104 < limit 105 → should fill
    await md.push(_book(bid="103", ask="104"))
    assert len(fills) == 1
    assert fills[0].price == Decimal("104")


async def test_cancel_order_removes_it_from_open() -> None:
    venue, _md, _clock = _build()
    await venue.start()
    oid = await venue.place_order(_limit_request(price="50"))
    assert len(await venue.get_open_orders()) == 1

    ok = await venue.cancel_order(oid)
    assert ok is True
    assert await venue.get_open_orders() == []


async def test_cancel_unknown_order_returns_false() -> None:
    venue, _md, _clock = _build()
    await venue.start()
    from trading.domain import OrderId

    assert await venue.cancel_order(OrderId("does-not-exist")) is False


async def test_get_order_returns_filled_order_in_history() -> None:
    venue, md, _clock = _build()
    await venue.start()
    await md.push(_book(bid="100", ask="101"))
    oid = await venue.place_order(_market_request(quantity="1"))

    order = await venue.get_order(oid)
    assert order is not None
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("1")


# ============================================================
# Position accounting
# ============================================================


async def test_buy_creates_long_position() -> None:
    venue, md, _clock = _build()
    await venue.start()
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="2"))

    positions = await venue.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.side.value == "long"
    assert p.quantity == Decimal("2")
    assert p.entry_price == Decimal("101")


async def test_sell_creates_short_position() -> None:
    venue, md, _clock = _build()
    await venue.start()
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.SELL, quantity="3"))

    positions = await venue.get_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.side.value == "short"
    assert p.quantity == Decimal("3")
    assert p.entry_price == Decimal("100")


async def test_adding_to_long_computes_weighted_avg_entry() -> None:
    venue, md, _clock = _build()
    await venue.start()
    # First buy 2 @ 101
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="2", cid="c-1"))
    # Then buy 3 @ 105 (book moves up)
    await md.push(_book(bid="104", ask="105", seq=2))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="3", cid="c-2"))

    positions = await venue.get_positions()
    assert positions[0].quantity == Decimal("5")
    # (2*101 + 3*105) / 5 = 103.4
    assert positions[0].entry_price == Decimal("103.4")


async def test_closing_long_realizes_pnl() -> None:
    venue, md, _clock = _build()
    await venue.start()
    # Buy 2 @ 101
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="2", cid="c-1"))
    # Sell 2 @ 110 (book moved up → profit)
    await md.push(_book(bid="110", ask="111", seq=2))
    await venue.place_order(_market_request(side=OrderSide.SELL, quantity="2", cid="c-2"))

    # Position should be flat; get_positions filters net=0
    assert await venue.get_positions() == []

    bal = await venue.get_balance()
    # PnL = (110 - 101) * 2 = 18, plus initial 10000 = 10018
    assert bal.total == Decimal("10018")


async def test_partial_close_keeps_remainder_at_original_entry() -> None:
    venue, md, _clock = _build()
    await venue.start()
    # Buy 5 @ 101
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="5", cid="c-1"))
    # Sell 2 @ 110 — closes 2/5 of the long
    await md.push(_book(bid="110", ask="111", seq=2))
    await venue.place_order(_market_request(side=OrderSide.SELL, quantity="2", cid="c-2"))

    positions = await venue.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == Decimal("3")
    # Entry stays at original average (101) — partial close doesn't recompute
    assert positions[0].entry_price == Decimal("101")
    # Realized PnL = (110 - 101) * 2 = 18
    assert positions[0].realized_pnl == Decimal("18")


async def test_flip_long_to_short_realizes_full_close_and_opens_new() -> None:
    venue, md, _clock = _build()
    await venue.start()
    # Buy 2 @ 101
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="2", cid="c-1"))
    # Sell 5 @ 110 → closes long 2 (PnL +18) and opens short 3 @ 110
    await md.push(_book(bid="110", ask="111", seq=2))
    await venue.place_order(_market_request(side=OrderSide.SELL, quantity="5", cid="c-2"))

    positions = await venue.get_positions()
    assert len(positions) == 1
    assert positions[0].side.value == "short"
    assert positions[0].quantity == Decimal("3")
    assert positions[0].entry_price == Decimal("110")
    assert positions[0].realized_pnl == Decimal("18")


# ============================================================
# Balance accounting
# ============================================================


async def test_initial_balance_with_no_positions() -> None:
    venue, _md, _clock = _build(initial_balance="5000")
    await venue.start()
    bal = await venue.get_balance()
    assert bal.total == Decimal("5000")


async def test_balance_includes_unrealized_pnl() -> None:
    venue, md, _clock = _build(initial_balance="10000")
    await venue.start()
    # Buy 1 @ 101
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="1"))

    # Move market up: mid = (110+111)/2 = 110.5
    await md.push(_book(bid="110", ask="111", seq=2))
    bal = await venue.get_balance()
    # Unrealized = (110.5 - 101) * 1 = 9.5; total = 10009.5
    assert bal.total == Decimal("10009.5")


async def test_balance_after_loss() -> None:
    venue, md, _clock = _build(initial_balance="10000")
    await venue.start()
    # Buy 1 @ 101
    await md.push(_book(bid="100", ask="101"))
    await venue.place_order(_market_request(side=OrderSide.BUY, quantity="1", cid="c-1"))
    # Sell at 95 (loss)
    await md.push(_book(bid="95", ask="96", seq=2))
    await venue.place_order(_market_request(side=OrderSide.SELL, quantity="1", cid="c-2"))

    bal = await venue.get_balance()
    # Realized PnL = (95 - 101) * 1 = -6; total = 9994
    assert bal.total == Decimal("9994")


# ============================================================
# Multiple symbols
# ============================================================


async def test_positions_isolated_per_symbol() -> None:
    venue, md, _clock = _build()
    await venue.start()
    await md.push(_book(symbol="BTCUSDT", bid="100", ask="101"))
    await md.push(_book(symbol="ETHUSDT", bid="3000", ask="3001"))
    await venue.place_order(_market_request(symbol="BTCUSDT", quantity="1", cid="c-1"))
    await venue.place_order(_market_request(symbol="ETHUSDT", quantity="2", cid="c-2"))

    positions = await venue.get_positions()
    by_symbol = {p.symbol: p for p in positions}
    assert by_symbol[Symbol("BTCUSDT")].quantity == Decimal("1")
    assert by_symbol[Symbol("ETHUSDT")].quantity == Decimal("2")


async def test_orderbook_for_one_symbol_does_not_affect_other() -> None:
    venue, md, _clock = _build()
    await venue.start()
    # Limit buy BTC at 105, no fill yet
    await venue.place_order(_limit_request(symbol="BTCUSDT", price="105", cid="c-1"))
    # ETH orderbook update — should not affect BTC order
    await md.push(_book(symbol="ETHUSDT", bid="3000", ask="3001"))
    assert len(await venue.get_open_orders(Symbol("BTCUSDT"))) == 1


# ============================================================
# POST_ONLY rejection at submission (TimeInForce, not OrderType)
# ============================================================


async def test_post_only_buy_rejected_when_would_cross_at_submission() -> None:
    """POST_ONLY buy at price >= best ask must be REJECTED, not accepted."""
    venue, md, _clock = _build()
    await venue.start()
    await md.push(_book(bid="100", ask="101"))

    # Buy at 105 — best ask 101 ≤ 105 → would immediately cross as taker
    oid = await venue.place_order(
        _limit_request(side=OrderSide.BUY, price="105", tif=TimeInForce.POST_ONLY)
    )

    order = await venue.get_order(oid)
    assert order is not None
    assert order.status == OrderStatus.REJECTED
    # Rejected orders don't appear in open
    assert await venue.get_open_orders() == []


async def test_post_only_sell_rejected_when_would_cross_at_submission() -> None:
    venue, md, _clock = _build()
    await venue.start()
    await md.push(_book(bid="100", ask="101"))

    # Sell at 99 — best bid 100 ≥ 99 → would cross
    oid = await venue.place_order(
        _limit_request(side=OrderSide.SELL, price="99", tif=TimeInForce.POST_ONLY)
    )
    order = await venue.get_order(oid)
    assert order is not None
    assert order.status == OrderStatus.REJECTED


async def test_post_only_accepted_when_not_crossing() -> None:
    """POST_ONLY that wouldn't cross at submission stays open as a normal limit."""
    venue, md, _clock = _build()
    await venue.start()
    await md.push(_book(bid="100", ask="101"))

    # Buy at 95 — best ask 101 > 95 → safely makes the book
    oid = await venue.place_order(
        _limit_request(side=OrderSide.BUY, price="95", tif=TimeInForce.POST_ONLY)
    )
    order = await venue.get_order(oid)
    assert order is not None
    assert order.status == OrderStatus.ACKED
    open_orders = await venue.get_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].order_id == oid


async def test_post_only_accepted_when_no_book_available() -> None:
    """Without a book snapshot we can't determine cross — accept (matching
    real-exchange behavior where an early order arrives before market
    data is built up)."""
    venue, _md, _clock = _build()
    await venue.start()

    oid = await venue.place_order(
        _limit_request(side=OrderSide.BUY, price="105", tif=TimeInForce.POST_ONLY)
    )
    order = await venue.get_order(oid)
    assert order is not None
    assert order.status == OrderStatus.ACKED


async def test_post_only_accepted_then_fills_via_later_orderbook_move() -> None:
    """Once accepted, POST_ONLY behaves like a regular limit order —
    fills when the market later crosses it (as a maker would in real life)."""
    venue, md, _clock = _build()
    await venue.start()
    # Initial book: ask=110, our POST_ONLY buy at 105 won't cross
    await md.push(_book(bid="109", ask="110"))
    oid = await venue.place_order(
        _limit_request(side=OrderSide.BUY, price="105", tif=TimeInForce.POST_ONLY)
    )
    order = await venue.get_order(oid)
    assert order is not None and order.status == OrderStatus.ACKED

    # Market later moves: ask=104 < our 105 → fills
    await md.push(_book(bid="103", ask="104", seq=2))

    order = await venue.get_order(oid)
    assert order is not None
    assert order.status == OrderStatus.FILLED
