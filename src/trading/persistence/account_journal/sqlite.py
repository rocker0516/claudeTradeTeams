"""SQLiteAccountJournal — durable position + balance snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import aiosqlite

from ...domain import Balance, Position, PositionSide, Symbol
from .base import AccountJournal

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    entry_price TEXT NOT NULL,
    mark_price TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    leverage TEXT NOT NULL,
    liquidation_price TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pos_symbol_updated
    ON position_snapshots(symbol, updated_at);

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    currency TEXT NOT NULL,
    total TEXT NOT NULL,
    available TEXT NOT NULL,
    margin_used TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bal_updated ON balance_snapshots(updated_at);
"""


class SQLiteAccountJournal(AccountJournal):
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def start(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _conn_or_raise(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SQLiteAccountJournal not started")
        return self._conn

    async def write_position_snapshot(self, position: Position) -> None:
        conn = self._conn_or_raise()
        await conn.execute(
            """
            INSERT INTO position_snapshots
            (symbol, side, quantity, entry_price, mark_price,
             unrealized_pnl, realized_pnl, leverage, liquidation_price,
             updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.symbol,
                position.side.value,
                str(position.quantity),
                str(position.entry_price),
                str(position.mark_price),
                str(position.unrealized_pnl),
                str(position.realized_pnl),
                str(position.leverage),
                str(position.liquidation_price) if position.liquidation_price is not None else None,
                position.updated_at.isoformat(),
            ),
        )
        await conn.commit()

    async def write_balance_snapshot(self, balance: Balance) -> None:
        conn = self._conn_or_raise()
        await conn.execute(
            """
            INSERT INTO balance_snapshots
            (currency, total, available, margin_used, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                balance.currency,
                str(balance.total),
                str(balance.available),
                str(balance.margin_used),
                balance.updated_at.isoformat(),
            ),
        )
        await conn.commit()

    async def read_latest_position(self, symbol: Symbol) -> Position | None:
        conn = self._conn_or_raise()
        cursor = await conn.execute(
            """
            SELECT symbol, side, quantity, entry_price, mark_price,
                   unrealized_pnl, realized_pnl, leverage, liquidation_price,
                   updated_at
            FROM position_snapshots
            WHERE symbol = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (symbol,),
        )
        row = await cursor.fetchone()
        return _row_to_position(row) if row is not None else None

    async def read_latest_balance(self) -> Balance | None:
        conn = self._conn_or_raise()
        cursor = await conn.execute(
            """
            SELECT currency, total, available, margin_used, updated_at
            FROM balance_snapshots
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        return _row_to_balance(row) if row is not None else None

    async def read_all_latest_positions(self) -> list[Position]:
        conn = self._conn_or_raise()
        # Window function: latest row per symbol by (updated_at, id)
        cursor = await conn.execute(
            """
            SELECT symbol, side, quantity, entry_price, mark_price,
                   unrealized_pnl, realized_pnl, leverage, liquidation_price,
                   updated_at
            FROM (
                SELECT *,
                    ROW_NUMBER() OVER (
                        PARTITION BY symbol
                        ORDER BY updated_at DESC, id DESC
                    ) AS rn
                FROM position_snapshots
            )
            WHERE rn = 1
            """
        )
        rows = await cursor.fetchall()
        return [_row_to_position(row) for row in rows]

    async def read_position_history(self, symbol: Symbol, since: datetime) -> list[Position]:
        conn = self._conn_or_raise()
        cursor = await conn.execute(
            """
            SELECT symbol, side, quantity, entry_price, mark_price,
                   unrealized_pnl, realized_pnl, leverage, liquidation_price,
                   updated_at
            FROM position_snapshots
            WHERE symbol = ? AND updated_at >= ?
            ORDER BY updated_at ASC, id ASC
            """,
            (symbol, since.isoformat()),
        )
        rows = await cursor.fetchall()
        return [_row_to_position(row) for row in rows]


def _row_to_position(row: aiosqlite.Row) -> Position:
    return Position(
        symbol=Symbol(row[0]),
        side=PositionSide(row[1]),
        quantity=Decimal(row[2]),
        entry_price=Decimal(row[3]),
        mark_price=Decimal(row[4]),
        unrealized_pnl=Decimal(row[5]),
        realized_pnl=Decimal(row[6]),
        leverage=Decimal(row[7]),
        liquidation_price=Decimal(row[8]) if row[8] is not None else None,
        updated_at=datetime.fromisoformat(row[9]),
    )


def _row_to_balance(row: aiosqlite.Row) -> Balance:
    return Balance(
        currency=row[0],
        total=Decimal(row[1]),
        available=Decimal(row[2]),
        margin_used=Decimal(row[3]),
        updated_at=datetime.fromisoformat(row[4]),
    )
