"""SQLiteAccountJournal contract tests + durability."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from trading.domain import Balance, Position, PositionSide, Symbol
from trading.persistence.account_journal import SQLiteAccountJournal

if TYPE_CHECKING:
    from pathlib import Path


def _position(
    symbol: str,
    ts: datetime,
    *,
    quantity: str = "0.5",
    liq: str | None = "30000",
) -> Position:
    return Position(
        symbol=Symbol(symbol),
        side=PositionSide.LONG,
        quantity=Decimal(quantity),
        entry_price=Decimal("60000"),
        mark_price=Decimal("60100"),
        unrealized_pnl=Decimal("50"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("2"),
        liquidation_price=Decimal(liq) if liq is not None else None,
        updated_at=ts,
    )


def _balance(ts: datetime, *, total: str = "1000") -> Balance:
    return Balance(
        currency="USDT",
        total=Decimal(total),
        available=Decimal("900"),
        margin_used=Decimal("100"),
        updated_at=ts,
    )


async def test_position_round_trip(tmp_path: Path) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    p = _position("BTCUSDT", now)
    await j.write_position_snapshot(p)

    got = await j.read_latest_position(Symbol("BTCUSDT"))
    assert got is not None
    assert got.symbol == p.symbol
    assert got.quantity == p.quantity
    assert got.entry_price == p.entry_price
    assert got.liquidation_price == p.liquidation_price
    assert got.updated_at == p.updated_at
    await j.stop()


async def test_balance_round_trip(tmp_path: Path) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    b = _balance(now)
    await j.write_balance_snapshot(b)

    got = await j.read_latest_balance()
    assert got is not None
    assert got.currency == b.currency
    assert got.total == b.total
    assert got.available == b.available
    assert got.margin_used == b.margin_used
    assert got.updated_at == b.updated_at
    await j.stop()


async def test_latest_position_picks_most_recent(tmp_path: Path) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    base = datetime.now(timezone.utc)
    await j.write_position_snapshot(_position("BTCUSDT", base, quantity="1"))
    await j.write_position_snapshot(_position("BTCUSDT", base + timedelta(seconds=1), quantity="2"))

    latest = await j.read_latest_position(Symbol("BTCUSDT"))
    assert latest is not None
    assert latest.quantity == Decimal("2")
    await j.stop()


async def test_position_history_filters_by_symbol_and_time(
    tmp_path: Path,
) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    base = datetime.now(timezone.utc)
    await j.write_position_snapshot(_position("BTCUSDT", base - timedelta(hours=1)))
    await j.write_position_snapshot(_position("BTCUSDT", base))
    await j.write_position_snapshot(_position("ETHUSDT", base))

    btc_recent = await j.read_position_history(Symbol("BTCUSDT"), base - timedelta(minutes=10))
    assert len(btc_recent) == 1
    assert btc_recent[0].symbol == Symbol("BTCUSDT")
    await j.stop()


async def test_position_with_no_liquidation_price(tmp_path: Path) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    now = datetime.now(timezone.utc)
    await j.write_position_snapshot(_position("BTCUSDT", now, liq=None))

    got = await j.read_latest_position(Symbol("BTCUSDT"))
    assert got is not None
    assert got.liquidation_price is None
    await j.stop()


async def test_read_all_latest_positions_returns_one_per_symbol(
    tmp_path: Path,
) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    base = datetime.now(timezone.utc)
    # BTC has 2 snapshots; ETH has 1
    await j.write_position_snapshot(_position("BTCUSDT", base - timedelta(hours=1), quantity="1"))
    await j.write_position_snapshot(_position("BTCUSDT", base, quantity="2"))
    await j.write_position_snapshot(_position("ETHUSDT", base, quantity="5"))

    result = await j.read_all_latest_positions()
    by_symbol = {p.symbol: p for p in result}
    assert set(by_symbol.keys()) == {Symbol("BTCUSDT"), Symbol("ETHUSDT")}
    # BTC latest should be quantity=2 (the newer one)
    assert by_symbol[Symbol("BTCUSDT")].quantity == Decimal("2")
    assert by_symbol[Symbol("ETHUSDT")].quantity == Decimal("5")
    await j.stop()


async def test_read_all_latest_positions_empty(tmp_path: Path) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    assert await j.read_all_latest_positions() == []
    await j.stop()


async def test_durability_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "durability.db"
    now = datetime.now(timezone.utc)

    j1 = SQLiteAccountJournal(db_path)
    await j1.start()
    await j1.write_position_snapshot(_position("BTCUSDT", now))
    await j1.write_balance_snapshot(_balance(now, total="5000"))
    await j1.stop()

    j2 = SQLiteAccountJournal(db_path)
    await j2.start()
    pos = await j2.read_latest_position(Symbol("BTCUSDT"))
    bal = await j2.read_latest_balance()
    assert pos is not None and pos.symbol == Symbol("BTCUSDT")
    assert bal is not None and bal.total == Decimal("5000")
    await j2.stop()


async def test_empty_returns_none(tmp_path: Path) -> None:
    j = SQLiteAccountJournal(tmp_path / "test.db")
    await j.start()
    assert await j.read_latest_position(Symbol("BTCUSDT")) is None
    assert await j.read_latest_balance() is None
    await j.stop()
