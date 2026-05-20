"""SQLiteOrderJournal contract tests + durability."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from trading.domain import (
    ClientOrderId,
    Fill,
    Order,
    OrderId,
    OrderSide,
    OrderStatus,
    OrderType,
    Symbol,
    TimeInForce,
)
from trading.persistence.order_journal import SQLiteOrderJournal

if TYPE_CHECKING:
    from pathlib import Path


def _order(
    order_id: str,
    status: OrderStatus,
    ts: datetime,
    *,
    symbol: str = "BTCUSDT",
) -> Order:
    return Order(
        order_id=OrderId(order_id),
        client_order_id=ClientOrderId(f"c-{order_id}"),
        symbol=Symbol(symbol),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        price=Decimal("60000"),
        average_fill_price=None,
        status=status,
        time_in_force=TimeInForce.GTC,
        reduce_only=False,
        created_at=ts,
        updated_at=ts,
    )


def _fill(order_id: str, ts: datetime) -> Fill:
    return Fill(
        order_id=OrderId(order_id),
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        price=Decimal("60000"),
        quantity=Decimal("0.5"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        is_maker=False,
        timestamp=ts,
    )


async def test_write_and_read_round_trip_preserves_all_fields(
    tmp_path: Path,
) -> None:
    j = SQLiteOrderJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    order = _order("o1", OrderStatus.SUBMITTED, now)
    await j.write_order(order)

    history = await j.read_order_history(OrderId("o1"))
    assert len(history) == 1
    got = history[0]
    # Verify EVERY field round-trips (especially Decimal + datetime)
    assert got.order_id == order.order_id
    assert got.client_order_id == order.client_order_id
    assert got.symbol == order.symbol
    assert got.side == order.side
    assert got.type == order.type
    assert got.quantity == order.quantity
    assert got.filled_quantity == order.filled_quantity
    assert got.price == order.price
    assert got.status == order.status
    assert got.time_in_force == order.time_in_force
    assert got.reduce_only == order.reduce_only
    assert got.created_at == order.created_at
    assert got.updated_at == order.updated_at

    await j.stop()


async def test_open_orders_excludes_terminal_statuses(tmp_path: Path) -> None:
    j = SQLiteOrderJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    await j.write_order(_order("filled", OrderStatus.FILLED, now))
    await j.write_order(_order("acked", OrderStatus.ACKED, now))
    await j.write_order(_order("partial", OrderStatus.PARTIALLY_FILLED, now))
    await j.write_order(_order("rejected", OrderStatus.REJECTED, now))

    open_orders = await j.read_open_orders()
    assert {o.order_id for o in open_orders} == {
        OrderId("acked"),
        OrderId("partial"),
    }

    await j.stop()


async def test_open_orders_uses_latest_state_per_order_id(
    tmp_path: Path,
) -> None:
    """If an order has SUBMITTED then later FILLED entries, only the FILLED
    is its current state and it should NOT appear in open orders."""
    j = SQLiteOrderJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    base = _order("o1", OrderStatus.SUBMITTED, now)
    await j.write_order(base)
    await j.write_order(
        replace(
            base,
            status=OrderStatus.FILLED,
            filled_quantity=Decimal("1"),
            updated_at=now + timedelta(seconds=1),
        )
    )

    assert await j.read_open_orders() == []
    history = await j.read_order_history(OrderId("o1"))
    assert len(history) == 2
    assert history[0].status == OrderStatus.SUBMITTED
    assert history[1].status == OrderStatus.FILLED

    await j.stop()


async def test_open_orders_filters_by_symbol(tmp_path: Path) -> None:
    j = SQLiteOrderJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    await j.write_order(_order("btc", OrderStatus.ACKED, now, symbol="BTCUSDT"))
    await j.write_order(_order("eth", OrderStatus.ACKED, now, symbol="ETHUSDT"))

    btc_open = await j.read_open_orders(Symbol("BTCUSDT"))
    assert len(btc_open) == 1
    assert btc_open[0].symbol == Symbol("BTCUSDT")

    await j.stop()


async def test_fills_since_time_threshold(tmp_path: Path) -> None:
    j = SQLiteOrderJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    await j.write_fill(_fill("o1", now - timedelta(hours=2)))
    await j.write_fill(_fill("o2", now))

    recent = await j.read_fills_since(now - timedelta(minutes=10))
    assert len(recent) == 1
    assert recent[0].order_id == OrderId("o2")

    await j.stop()


async def test_durability_survives_restart(tmp_path: Path) -> None:
    """THE durability test: write, close DB, reopen — data must still be there."""
    db_path = tmp_path / "durability.db"
    now = datetime.now(timezone.utc)

    j1 = SQLiteOrderJournal(db_path)
    await j1.start()
    await j1.write_order(_order("persistent", OrderStatus.ACKED, now))
    await j1.write_fill(_fill("persistent", now))
    await j1.stop()

    # Reopen
    j2 = SQLiteOrderJournal(db_path)
    await j2.start()
    open_orders = await j2.read_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].order_id == OrderId("persistent")
    fills = await j2.read_fills_since(now - timedelta(seconds=10))
    assert len(fills) == 1
    await j2.stop()


async def test_empty_journal_returns_empty_lists(tmp_path: Path) -> None:
    j = SQLiteOrderJournal(tmp_path / "test.db")
    await j.start()
    assert await j.read_open_orders() == []
    assert await j.read_order_history(OrderId("nope")) == []
    assert await j.read_fills_since(datetime.now(timezone.utc)) == []
    await j.stop()


async def test_methods_raise_before_start(tmp_path: Path) -> None:
    """Pre-start, calls should fail loud rather than silently succeed."""
    import pytest

    j = SQLiteOrderJournal(tmp_path / "test.db")
    with pytest.raises(RuntimeError, match="not started"):
        await j.read_open_orders()
