"""SQLiteOrderJournal — durable order events + fills, append-only.

Stores Decimal as TEXT (SQLite REAL is double-precision float, lossy
for financial values). Datetimes as ISO 8601 strings.

Opens its own aiosqlite connection in `start()`. WAL mode lets multiple
SQLite-backed journals share the same database file with concurrent
readers + serialized writer.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import aiosqlite

from ...domain import (
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
from .base import OrderJournal

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    filled_quantity TEXT NOT NULL,
    price TEXT,
    average_fill_price TEXT,
    status TEXT NOT NULL,
    time_in_force TEXT NOT NULL,
    reduce_only INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_events_order_id ON order_events(order_id);
CREATE INDEX IF NOT EXISTS idx_order_events_symbol ON order_events(symbol);
CREATE INDEX IF NOT EXISTS idx_order_events_updated_at ON order_events(updated_at);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price TEXT NOT NULL,
    quantity TEXT NOT NULL,
    fee TEXT NOT NULL,
    fee_currency TEXT NOT NULL,
    is_maker INTEGER NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_timestamp ON fills(timestamp);
"""

_TERMINAL_STATUS_VALUES = (
    OrderStatus.FILLED.value,
    OrderStatus.CANCELLED.value,
    OrderStatus.REJECTED.value,
    OrderStatus.EXPIRED.value,
    OrderStatus.FAILED.value,
)


class SQLiteOrderJournal(OrderJournal):
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
            raise RuntimeError("SQLiteOrderJournal not started")
        return self._conn

    async def write_order(self, order: Order) -> None:
        conn = self._conn_or_raise()
        await conn.execute(
            """
            INSERT INTO order_events
            (order_id, client_order_id, symbol, side, type, quantity,
             filled_quantity, price, average_fill_price, status,
             time_in_force, reduce_only, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.client_order_id,
                order.symbol,
                order.side.value,
                order.type.value,
                str(order.quantity),
                str(order.filled_quantity),
                str(order.price) if order.price is not None else None,
                str(order.average_fill_price) if order.average_fill_price is not None else None,
                order.status.value,
                order.time_in_force.value,
                int(order.reduce_only),
                order.created_at.isoformat(),
                order.updated_at.isoformat(),
            ),
        )
        await conn.commit()

    async def write_fill(self, fill: Fill) -> None:
        conn = self._conn_or_raise()
        await conn.execute(
            """
            INSERT INTO fills
            (order_id, symbol, side, price, quantity, fee, fee_currency,
             is_maker, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.order_id,
                fill.symbol,
                fill.side.value,
                str(fill.price),
                str(fill.quantity),
                str(fill.fee),
                fill.fee_currency,
                int(fill.is_maker),
                fill.timestamp.isoformat(),
            ),
        )
        await conn.commit()

    async def read_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        conn = self._conn_or_raise()
        # Per order_id, take latest row by (updated_at, id), then filter terminal.
        placeholders = ",".join("?" for _ in _TERMINAL_STATUS_VALUES)
        query = f"""
        SELECT order_id, client_order_id, symbol, side, type, quantity,
               filled_quantity, price, average_fill_price, status,
               time_in_force, reduce_only, created_at, updated_at
        FROM (
            SELECT *,
                ROW_NUMBER() OVER (
                    PARTITION BY order_id
                    ORDER BY updated_at DESC, id DESC
                ) AS rn
            FROM order_events
        )
        WHERE rn = 1 AND status NOT IN ({placeholders})
        """
        params: list[object] = list(_TERMINAL_STATUS_VALUES)
        if symbol is not None:
            query += " AND symbol = ?"
            params.append(symbol)
        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_order(row) for row in rows]

    async def read_order_history(self, order_id: OrderId) -> list[Order]:
        conn = self._conn_or_raise()
        cursor = await conn.execute(
            """
            SELECT order_id, client_order_id, symbol, side, type, quantity,
                   filled_quantity, price, average_fill_price, status,
                   time_in_force, reduce_only, created_at, updated_at
            FROM order_events
            WHERE order_id = ?
            ORDER BY updated_at ASC, id ASC
            """,
            (order_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_order(row) for row in rows]

    async def read_fills_since(self, since: datetime) -> list[Fill]:
        conn = self._conn_or_raise()
        cursor = await conn.execute(
            """
            SELECT order_id, symbol, side, price, quantity, fee, fee_currency,
                   is_maker, timestamp
            FROM fills
            WHERE timestamp >= ?
            ORDER BY timestamp ASC, id ASC
            """,
            (since.isoformat(),),
        )
        rows = await cursor.fetchall()
        return [_row_to_fill(row) for row in rows]


def _row_to_order(row: aiosqlite.Row) -> Order:
    return Order(
        order_id=OrderId(row[0]),
        client_order_id=ClientOrderId(row[1]),
        symbol=Symbol(row[2]),
        side=OrderSide(row[3]),
        type=OrderType(row[4]),
        quantity=Decimal(row[5]),
        filled_quantity=Decimal(row[6]),
        price=Decimal(row[7]) if row[7] is not None else None,
        average_fill_price=Decimal(row[8]) if row[8] is not None else None,
        status=OrderStatus(row[9]),
        time_in_force=TimeInForce(row[10]),
        reduce_only=bool(row[11]),
        created_at=datetime.fromisoformat(row[12]),
        updated_at=datetime.fromisoformat(row[13]),
    )


def _row_to_fill(row: aiosqlite.Row) -> Fill:
    return Fill(
        order_id=OrderId(row[0]),
        symbol=Symbol(row[1]),
        side=OrderSide(row[2]),
        price=Decimal(row[3]),
        quantity=Decimal(row[4]),
        fee=Decimal(row[5]),
        fee_currency=row[6],
        is_maker=bool(row[7]),
        timestamp=datetime.fromisoformat(row[8]),
    )
