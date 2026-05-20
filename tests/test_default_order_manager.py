"""DefaultOrderManager contract tests."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from decimal import Decimal

from trading.clock import RealClock
from trading.domain import (
    Balance,
    ClientOrderId,
    Fill,
    Order,
    OrderId,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    SubmissionFailed,
    Submitted,
    Symbol,
    TimeInForce,
    TradeIntent,
)
from trading.oms import DefaultOrderManager
from trading.persistence.order_journal import InMemoryOrderJournal
from trading.venues import Venue


class _FakeVenue(Venue):
    """Records calls and lets tests fire callbacks manually."""

    def __init__(self) -> None:
        self._next = 0
        self._order_cbs: list[Callable[[Order], Awaitable[None]]] = []
        self._fill_cbs: list[Callable[[Fill], Awaitable[None]]] = []
        self.placed: list[OrderRequest] = []
        self.cancelled: list[OrderId] = []
        self.cancel_succeeds = True
        self.raise_on_place: Exception | None = None

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def place_order(self, request: OrderRequest) -> OrderId:
        if self.raise_on_place is not None:
            raise self.raise_on_place
        self._next += 1
        oid = OrderId(f"v-{self._next}")
        self.placed.append(request)
        return oid

    async def cancel_order(self, order_id: OrderId) -> bool:
        self.cancelled.append(order_id)
        return self.cancel_succeeds

    async def get_order(self, order_id: OrderId) -> Order | None:
        return None

    async def get_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        return []

    async def get_positions(self) -> list[Position]:
        return []

    async def get_balance(self) -> Balance:
        return Balance(
            "USDT",
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            datetime.now(timezone.utc),
        )

    def on_order_update(self, callback: Callable[[Order], Awaitable[None]]) -> None:
        self._order_cbs.append(callback)

    def on_fill(self, callback: Callable[[Fill], Awaitable[None]]) -> None:
        self._fill_cbs.append(callback)

    async def fire_order_update(self, order: Order) -> None:
        for cb in self._order_cbs:
            await cb(order)

    async def fire_fill(self, fill: Fill) -> None:
        for cb in self._fill_cbs:
            await cb(fill)


def _intent(symbol: str = "BTCUSDT", quantity: str = "1") -> TradeIntent:
    return TradeIntent(
        symbol=Symbol(symbol),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        price=Decimal("60000"),
        time_in_force=TimeInForce.GTC,
    )


def _build(
    *, cid_factory: Callable[[], ClientOrderId] | None = None
) -> tuple[DefaultOrderManager, _FakeVenue, InMemoryOrderJournal]:
    venue = _FakeVenue()
    journal = InMemoryOrderJournal()
    clock = RealClock()
    oms = DefaultOrderManager(venue=venue, journal=journal, clock=clock, cid_factory=cid_factory)
    return oms, venue, journal


async def test_submit_returns_submitted_with_venue_order_id() -> None:
    oms, _venue, _journal = _build()
    result = await oms.submit(_intent())
    assert isinstance(result, Submitted)
    assert result.order_id == OrderId("v-1")
    assert result.client_order_id.startswith("oms-")


async def test_submit_translates_intent_to_order_request() -> None:
    oms, venue, _journal = _build()
    await oms.submit(_intent())
    assert len(venue.placed) == 1
    req = venue.placed[0]
    assert req.symbol == Symbol("BTCUSDT")
    assert req.quantity == Decimal("1")
    assert req.price == Decimal("60000")
    assert req.time_in_force == TimeInForce.GTC


async def test_submit_writes_initial_state_to_journal() -> None:
    oms, _venue, journal = _build()
    await oms.submit(_intent())
    orders = await journal.read_open_orders()
    assert len(orders) == 1
    assert orders[0].status == OrderStatus.SUBMITTED
    assert orders[0].filled_quantity == Decimal("0")


async def test_submit_assigns_unique_cids() -> None:
    oms, _venue, _journal = _build()
    r1 = await oms.submit(_intent())
    r2 = await oms.submit(_intent())
    assert isinstance(r1, Submitted)
    assert isinstance(r2, Submitted)
    assert r1.client_order_id != r2.client_order_id


async def test_submit_returns_submission_failed_on_venue_exception() -> None:
    oms, venue, _journal = _build()
    venue.raise_on_place = ConnectionError("net down")
    result = await oms.submit(_intent())
    assert isinstance(result, SubmissionFailed)
    assert result.is_retryable is True
    assert "net down" in result.reason


async def test_cancel_delegates_to_venue() -> None:
    oms, venue, _journal = _build()
    ok = await oms.cancel(OrderId("v-1"))
    assert ok is True
    assert venue.cancelled == [OrderId("v-1")]


async def test_cancel_returns_false_when_venue_refuses() -> None:
    oms, venue, _journal = _build()
    venue.cancel_succeeds = False
    assert await oms.cancel(OrderId("v-1")) is False


async def test_cancel_all_cancels_every_open_order() -> None:
    oms, venue, _journal = _build()
    await oms.submit(_intent())
    await oms.submit(_intent())
    await oms.submit(_intent())

    n = await oms.cancel_all()
    assert n == 3
    assert len(venue.cancelled) == 3


async def test_cancel_all_filters_by_symbol() -> None:
    oms, _venue, _journal = _build()
    await oms.submit(_intent("BTCUSDT"))
    await oms.submit(_intent("BTCUSDT"))
    await oms.submit(_intent("ETHUSDT"))

    n = await oms.cancel_all(Symbol("BTCUSDT"))
    assert n == 2


async def test_on_fill_increments_filled_quantity() -> None:
    oms, venue, _journal = _build()
    result = await oms.submit(_intent(quantity="10"))
    assert isinstance(result, Submitted)
    now = datetime.now(timezone.utc)

    await venue.fire_fill(
        Fill(
            order_id=result.order_id,
            symbol=Symbol("BTCUSDT"),
            side=OrderSide.BUY,
            price=Decimal("60000"),
            quantity=Decimal("3"),
            fee=Decimal("0.01"),
            fee_currency="USDT",
            is_maker=False,
            timestamp=now,
        )
    )

    order = await oms.get_order(result.order_id)
    assert order is not None
    assert order.filled_quantity == Decimal("3")
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.average_fill_price == Decimal("60000")


async def test_on_fill_marks_filled_when_quantity_complete() -> None:
    oms, venue, _journal = _build()
    result = await oms.submit(_intent(quantity="2"))
    assert isinstance(result, Submitted)
    now = datetime.now(timezone.utc)

    await venue.fire_fill(
        Fill(
            order_id=result.order_id,
            symbol=Symbol("BTCUSDT"),
            side=OrderSide.BUY,
            price=Decimal("60000"),
            quantity=Decimal("2"),
            fee=Decimal("0.01"),
            fee_currency="USDT",
            is_maker=False,
            timestamp=now,
        )
    )

    order = await oms.get_order(result.order_id)
    assert order is not None
    assert order.status == OrderStatus.FILLED


async def test_on_fill_computes_weighted_average_price() -> None:
    """Two fills at different prices -> weighted average."""
    oms, venue, _journal = _build()
    result = await oms.submit(_intent(quantity="10"))
    assert isinstance(result, Submitted)
    now = datetime.now(timezone.utc)

    # Fill 4 @ 60000, then 6 @ 61000 → avg = (4*60000 + 6*61000) / 10 = 60600
    await venue.fire_fill(
        Fill(
            order_id=result.order_id,
            symbol=Symbol("BTCUSDT"),
            side=OrderSide.BUY,
            price=Decimal("60000"),
            quantity=Decimal("4"),
            fee=Decimal("0"),
            fee_currency="USDT",
            is_maker=False,
            timestamp=now,
        )
    )
    await venue.fire_fill(
        Fill(
            order_id=result.order_id,
            symbol=Symbol("BTCUSDT"),
            side=OrderSide.BUY,
            price=Decimal("61000"),
            quantity=Decimal("6"),
            fee=Decimal("0"),
            fee_currency="USDT",
            is_maker=False,
            timestamp=now,
        )
    )

    order = await oms.get_order(result.order_id)
    assert order is not None
    assert order.filled_quantity == Decimal("10")
    assert order.average_fill_price == Decimal("60600")


async def test_on_fill_writes_to_journal() -> None:
    oms, venue, journal = _build()
    result = await oms.submit(_intent(quantity="10"))
    assert isinstance(result, Submitted)
    now = datetime.now(timezone.utc)

    fill = Fill(
        order_id=result.order_id,
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        price=Decimal("60000"),
        quantity=Decimal("1"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        is_maker=False,
        timestamp=now,
    )
    await venue.fire_fill(fill)

    from datetime import timedelta

    fills = await journal.read_fills_since(now - timedelta(seconds=10))
    assert len(fills) == 1
    assert fills[0].order_id == result.order_id


async def test_on_order_update_overwrites_cached_state() -> None:
    oms, venue, _journal = _build()
    result = await oms.submit(_intent())
    assert isinstance(result, Submitted)

    # Venue tells us the order is now ACKED
    cached = await oms.get_order(result.order_id)
    assert cached is not None
    from dataclasses import replace

    acked = replace(cached, status=OrderStatus.ACKED)
    await venue.fire_order_update(acked)

    updated = await oms.get_order(result.order_id)
    assert updated is not None
    assert updated.status == OrderStatus.ACKED


async def test_on_fill_for_unknown_order_does_not_crash() -> None:
    """Fill arrives for an order we never tracked (e.g., recovery scenario)."""
    _oms, venue, journal = _build()
    now = datetime.now(timezone.utc)
    fill = Fill(
        order_id=OrderId("v-unknown"),
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        price=Decimal("60000"),
        quantity=Decimal("1"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        is_maker=False,
        timestamp=now,
    )
    await venue.fire_fill(fill)
    # Fill is still journaled even without matching order
    from datetime import timedelta

    fills = await journal.read_fills_since(now - timedelta(seconds=10))
    assert len(fills) == 1


async def test_deterministic_cid_factory_for_testing() -> None:
    """Custom cid_factory enables deterministic tests."""
    counter = iter(range(1000))

    def factory() -> ClientOrderId:
        return ClientOrderId(f"test-{next(counter):03d}")

    oms, _venue, _journal = _build(cid_factory=factory)
    r1 = await oms.submit(_intent())
    r2 = await oms.submit(_intent())
    assert isinstance(r1, Submitted)
    assert isinstance(r2, Submitted)
    assert r1.client_order_id == ClientOrderId("test-000")
    assert r2.client_order_id == ClientOrderId("test-001")


async def test_open_orders_excludes_terminal_statuses() -> None:
    oms, venue, _journal = _build()
    r1 = await oms.submit(_intent(quantity="1"))
    r2 = await oms.submit(_intent(quantity="2"))
    assert isinstance(r1, Submitted) and isinstance(r2, Submitted)

    # Fill r1 completely
    now = datetime.now(timezone.utc)
    await venue.fire_fill(
        Fill(
            order_id=r1.order_id,
            symbol=Symbol("BTCUSDT"),
            side=OrderSide.BUY,
            price=Decimal("60000"),
            quantity=Decimal("1"),
            fee=Decimal("0"),
            fee_currency="USDT",
            is_maker=False,
            timestamp=now,
        )
    )

    open_orders = await oms.open_orders()
    assert {o.order_id for o in open_orders} == {r2.order_id}


# ============================================================
# Restore from journal (Lifecycle start)
# ============================================================


async def test_start_restores_open_orders_from_journal() -> None:
    """A fresh OMS pointed at a populated journal sees the restored
    state via get_order() / open_orders() after start()."""
    from dataclasses import replace

    journal = InMemoryOrderJournal()
    now = datetime.now(timezone.utc)

    # Pre-populate journal with one open order and one filled order
    open_order = Order(
        order_id=OrderId("v-1"),
        client_order_id=ClientOrderId("c-1"),
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        price=Decimal("60000"),
        average_fill_price=None,
        status=OrderStatus.ACKED,
        time_in_force=TimeInForce.GTC,
        reduce_only=False,
        created_at=now,
        updated_at=now,
    )
    filled_order = replace(
        open_order,
        order_id=OrderId("v-2"),
        client_order_id=ClientOrderId("c-2"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("1"),
        average_fill_price=Decimal("60000"),
    )
    await journal.write_order(open_order)
    await journal.write_order(filled_order)

    # New OMS pointing at the same journal
    venue = _FakeVenue()
    oms = DefaultOrderManager(venue=venue, journal=journal, clock=RealClock())
    await oms.start()

    # Open order should be restored to cache
    restored = await oms.get_order(OrderId("v-1"))
    assert restored is not None
    assert restored.status == OrderStatus.ACKED

    # Filled order is terminal — should NOT appear in open_orders
    open_orders = await oms.open_orders()
    assert {o.order_id for o in open_orders} == {OrderId("v-1")}


async def test_start_with_empty_journal_leaves_cache_empty() -> None:
    venue = _FakeVenue()
    journal = InMemoryOrderJournal()
    oms = DefaultOrderManager(venue=venue, journal=journal, clock=RealClock())
    await oms.start()
    assert await oms.open_orders() == []
