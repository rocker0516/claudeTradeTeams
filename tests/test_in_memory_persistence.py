"""In-memory persistence implementation contract tests.

One test module per ABC would be cleaner long-term, but at this scale
keeping them together makes the cross-cutting behaviors easier to
read.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.domain import (
    Alert,
    AlertLevel,
    Balance,
    ClientOrderId,
    Fill,
    Order,
    OrderId,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
    Symbol,
    TimeInForce,
)
from trading.persistence.account_journal import InMemoryAccountJournal
from trading.persistence.alert_log import InMemoryAlertLog
from trading.persistence.event_log import InMemoryEventLog
from trading.persistence.order_journal import InMemoryOrderJournal
from trading.runtime import SystemState, SystemStateChanged


def _make_order(order_id: str, status: OrderStatus, ts: datetime) -> Order:
    return Order(
        order_id=OrderId(order_id),
        client_order_id=ClientOrderId(f"c-{order_id}"),
        symbol=Symbol("BTCUSDT"),
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


def _make_balance(now: datetime) -> Balance:
    return Balance(
        currency="USDT",
        total=Decimal("1000"),
        available=Decimal("900"),
        margin_used=Decimal("100"),
        updated_at=now,
    )


def _make_position(symbol: str, ts: datetime) -> Position:
    return Position(
        symbol=Symbol(symbol),
        side=PositionSide.LONG,
        quantity=Decimal("0.5"),
        entry_price=Decimal("60000"),
        mark_price=Decimal("60100"),
        unrealized_pnl=Decimal("50"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("2"),
        liquidation_price=Decimal("30000"),
        updated_at=ts,
    )


# ============================================================
# OrderJournal
# ============================================================


async def test_order_journal_returns_full_history_in_order() -> None:
    j = InMemoryOrderJournal()
    now = datetime.now(timezone.utc)
    o = _make_order("o1", OrderStatus.SUBMITTED, now)
    o_acked = replace(o, status=OrderStatus.ACKED, updated_at=now + timedelta(seconds=1))
    await j.write_order(o)
    await j.write_order(o_acked)

    history = await j.read_order_history(OrderId("o1"))
    assert [h.status for h in history] == [OrderStatus.SUBMITTED, OrderStatus.ACKED]


async def test_order_journal_open_excludes_all_terminal_statuses() -> None:
    j = InMemoryOrderJournal()
    now = datetime.now(timezone.utc)
    await j.write_order(_make_order("filled", OrderStatus.FILLED, now))
    await j.write_order(_make_order("cancelled", OrderStatus.CANCELLED, now))
    await j.write_order(_make_order("rejected", OrderStatus.REJECTED, now))
    await j.write_order(_make_order("expired", OrderStatus.EXPIRED, now))
    await j.write_order(_make_order("failed", OrderStatus.FAILED, now))
    await j.write_order(_make_order("acked", OrderStatus.ACKED, now))
    await j.write_order(_make_order("partial", OrderStatus.PARTIALLY_FILLED, now))

    open_orders = await j.read_open_orders()
    assert {o.order_id for o in open_orders} == {OrderId("acked"), OrderId("partial")}


async def test_order_journal_filters_by_symbol() -> None:
    j = InMemoryOrderJournal()
    now = datetime.now(timezone.utc)
    btc = _make_order("btc", OrderStatus.ACKED, now)
    eth = replace(_make_order("eth", OrderStatus.ACKED, now), symbol=Symbol("ETHUSDT"))
    await j.write_order(btc)
    await j.write_order(eth)

    btc_open = await j.read_open_orders(Symbol("BTCUSDT"))
    assert len(btc_open) == 1
    assert btc_open[0].symbol == Symbol("BTCUSDT")


async def test_order_journal_fills_since_time_threshold() -> None:
    j = InMemoryOrderJournal()
    now = datetime.now(timezone.utc)
    f_old = Fill(
        order_id=OrderId("o1"),
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        price=Decimal("60000"),
        quantity=Decimal("0.5"),
        fee=Decimal("0.01"),
        fee_currency="USDT",
        is_maker=False,
        timestamp=now - timedelta(seconds=60),
    )
    f_new = replace(f_old, timestamp=now)
    await j.write_fill(f_old)
    await j.write_fill(f_new)

    recent = await j.read_fills_since(now - timedelta(seconds=10))
    assert len(recent) == 1
    assert recent[0] is f_new


# ============================================================
# AccountJournal
# ============================================================


async def test_account_journal_returns_latest_position() -> None:
    j = InMemoryAccountJournal()
    now = datetime.now(timezone.utc)
    p1 = _make_position("BTCUSDT", now - timedelta(seconds=60))
    p2 = _make_position("BTCUSDT", now)
    await j.write_position_snapshot(p1)
    await j.write_position_snapshot(p2)

    assert (await j.read_latest_position(Symbol("BTCUSDT"))) is p2


async def test_account_journal_returns_none_when_no_position() -> None:
    j = InMemoryAccountJournal()
    assert await j.read_latest_position(Symbol("BTCUSDT")) is None


async def test_account_journal_returns_latest_balance() -> None:
    j = InMemoryAccountJournal()
    now = datetime.now(timezone.utc)
    b1 = _make_balance(now - timedelta(seconds=60))
    b2 = _make_balance(now)
    await j.write_balance_snapshot(b1)
    await j.write_balance_snapshot(b2)

    assert (await j.read_latest_balance()) is b2


async def test_account_journal_read_all_latest_positions_one_per_symbol() -> None:
    """For each symbol with any snapshot history, return the most recent."""
    j = InMemoryAccountJournal()
    now = datetime.now(timezone.utc)
    # BTC has 2 snapshots, latest at `now`
    await j.write_position_snapshot(_make_position("BTCUSDT", now - timedelta(hours=1)))
    btc_latest = _make_position("BTCUSDT", now)
    await j.write_position_snapshot(btc_latest)
    # ETH has 1 snapshot
    eth_only = _make_position("ETHUSDT", now)
    await j.write_position_snapshot(eth_only)

    result = await j.read_all_latest_positions()
    by_symbol = {p.symbol: p for p in result}
    assert set(by_symbol.keys()) == {Symbol("BTCUSDT"), Symbol("ETHUSDT")}
    # BTC entry must be the LATER one (use identity check since they differ only in ts)
    assert by_symbol[Symbol("BTCUSDT")] is btc_latest
    assert by_symbol[Symbol("ETHUSDT")] is eth_only


async def test_account_journal_read_all_latest_positions_empty() -> None:
    j = InMemoryAccountJournal()
    assert await j.read_all_latest_positions() == []


async def test_account_journal_position_history_filters_symbol_and_time() -> None:
    j = InMemoryAccountJournal()
    now = datetime.now(timezone.utc)
    btc_old = _make_position("BTCUSDT", now - timedelta(seconds=120))
    btc_new = _make_position("BTCUSDT", now)
    eth_new = _make_position("ETHUSDT", now)
    await j.write_position_snapshot(btc_old)
    await j.write_position_snapshot(btc_new)
    await j.write_position_snapshot(eth_new)

    btc_recent = await j.read_position_history(Symbol("BTCUSDT"), now - timedelta(seconds=60))
    assert len(btc_recent) == 1
    assert btc_recent[0] is btc_new


# ============================================================
# EventLog
# ============================================================


async def test_event_log_filters_by_type_and_time() -> None:
    log = InMemoryEventLog()
    now = datetime.now(timezone.utc)
    ev_old = SystemStateChanged(
        SystemState.BOOTING,
        SystemState.RUNNING,
        "old",
        now - timedelta(seconds=60),
    )
    ev_new = SystemStateChanged(
        SystemState.RUNNING,
        SystemState.QUARANTINE,
        "new",
        now,
    )
    await log.write(ev_old)
    await log.write(ev_new)

    recent = await log.read_since(SystemStateChanged, now - timedelta(seconds=10))
    assert len(recent) == 1
    assert recent[0] is ev_new


async def test_event_log_rejects_events_without_timestamp() -> None:
    log = InMemoryEventLog()
    with pytest.raises(TypeError, match="timestamp"):
        await log.write(object())


# ============================================================
# AlertLog
# ============================================================


async def test_alert_log_filters_by_level() -> None:
    log = InMemoryAlertLog()
    now = datetime.now(timezone.utc)
    a_info = Alert(AlertLevel.INFO, "X", "info", now)
    a_crit = Alert(AlertLevel.CRITICAL, "X", "boom", now)
    await log.write(a_info)
    await log.write(a_crit)

    crit = await log.read_since(now - timedelta(seconds=10), level=AlertLevel.CRITICAL)
    assert len(crit) == 1
    assert crit[0].level == AlertLevel.CRITICAL


async def test_alert_log_filters_by_component() -> None:
    log = InMemoryAlertLog()
    now = datetime.now(timezone.utc)
    oms = Alert(AlertLevel.WARN, "OMS", "x", now)
    pms = Alert(AlertLevel.WARN, "PMS", "y", now)
    await log.write(oms)
    await log.write(pms)

    oms_only = await log.read_since(now - timedelta(seconds=10), component="OMS")
    assert len(oms_only) == 1
    assert oms_only[0].component == "OMS"


async def test_alert_log_filters_by_level_and_component_combined() -> None:
    log = InMemoryAlertLog()
    now = datetime.now(timezone.utc)
    await log.write(Alert(AlertLevel.INFO, "OMS", "info-oms", now))
    await log.write(Alert(AlertLevel.WARN, "OMS", "warn-oms", now))
    await log.write(Alert(AlertLevel.WARN, "PMS", "warn-pms", now))

    result = await log.read_since(
        now - timedelta(seconds=10),
        level=AlertLevel.WARN,
        component="OMS",
    )
    assert len(result) == 1
    assert result[0].message == "warn-oms"
