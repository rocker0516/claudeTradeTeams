"""SQLiteEventLog — durable runtime event audit log.

Events are stored as (event_type, payload_json, timestamp). The payload
serialization (Decimal / datetime / Enum) is handled by `_serde.py` —
see that module for the encoder's limits.

read_since uses the event_type's class name as a discriminator; calling
`read_since(SystemStateChanged, since)` returns only rows with
`event_type = "SystemStateChanged"`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

import aiosqlite

from .._serde import decode_dataclass, encode_dataclass
from .base import EventLog

if TYPE_CHECKING:
    from pathlib import Path

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_type_timestamp
    ON events(event_type, timestamp);
"""


class SQLiteEventLog(EventLog):
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
            raise RuntimeError("SQLiteEventLog not started")
        return self._conn

    async def write(self, event: object) -> None:
        ts = getattr(event, "timestamp", None)
        if ts is None or not isinstance(ts, datetime):
            raise TypeError(
                f"event must have a 'timestamp: datetime' attribute; got {type(event).__name__}"
            )
        conn = self._conn_or_raise()
        await conn.execute(
            "INSERT INTO events (event_type, payload, timestamp) VALUES (?, ?, ?)",
            (
                type(event).__name__,
                encode_dataclass(event),
                ts.isoformat(),
            ),
        )
        await conn.commit()

    async def read_since(
        self,
        event_type: type[T],
        since: datetime,
    ) -> list[T]:
        conn = self._conn_or_raise()
        cursor = await conn.execute(
            """
            SELECT payload FROM events
            WHERE event_type = ? AND timestamp >= ?
            ORDER BY timestamp ASC, id ASC
            """,
            (event_type.__name__, since.isoformat()),
        )
        rows = await cursor.fetchall()
        return [decode_dataclass(event_type, row[0]) for row in rows]
