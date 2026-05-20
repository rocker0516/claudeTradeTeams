"""SQLiteAlertLog — durable record of every emitted Alert."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite

from ...domain import Alert, AlertLevel
from .base import AlertLog

if TYPE_CHECKING:
    from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    correlation_key TEXT,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_component ON alerts(component);
CREATE INDEX IF NOT EXISTS idx_alerts_correlation_key
    ON alerts(correlation_key);
"""


class SQLiteAlertLog(AlertLog):
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
            raise RuntimeError("SQLiteAlertLog not started")
        return self._conn

    async def write(self, alert: Alert) -> None:
        conn = self._conn_or_raise()
        await conn.execute(
            """
            INSERT INTO alerts
            (level, component, message, correlation_key, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                alert.level.value,
                alert.component,
                alert.message,
                alert.correlation_key,
                alert.timestamp.isoformat(),
            ),
        )
        await conn.commit()

    async def read_since(
        self,
        since: datetime,
        *,
        level: AlertLevel | None = None,
        component: str | None = None,
    ) -> list[Alert]:
        conn = self._conn_or_raise()
        clauses = ["timestamp >= ?"]
        params: list[object] = [since.isoformat()]
        if level is not None:
            clauses.append("level = ?")
            params.append(level.value)
        if component is not None:
            clauses.append("component = ?")
            params.append(component)
        query = (
            "SELECT level, component, message, correlation_key, timestamp "
            "FROM alerts WHERE " + " AND ".join(clauses) + " ORDER BY timestamp ASC, id ASC"
        )
        cursor = await conn.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_alert(row) for row in rows]


def _row_to_alert(row: aiosqlite.Row) -> Alert:
    return Alert(
        level=AlertLevel(row[0]),
        component=row[1],
        message=row[2],
        correlation_key=row[3],
        timestamp=datetime.fromisoformat(row[4]),
    )
