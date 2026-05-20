"""SQLiteAlertLog contract tests + durability."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from trading.domain import Alert, AlertLevel
from trading.persistence.alert_log import SQLiteAlertLog

if TYPE_CHECKING:
    from pathlib import Path


def _alert(
    *,
    level: AlertLevel = AlertLevel.INFO,
    component: str = "X",
    message: str = "msg",
    correlation_key: str | None = None,
    ts: datetime | None = None,
) -> Alert:
    return Alert(
        level=level,
        component=component,
        message=message,
        timestamp=ts or datetime.now(timezone.utc),
        correlation_key=correlation_key,
    )


async def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    log = SQLiteAlertLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    a = _alert(
        level=AlertLevel.CRITICAL,
        component="Supervisor",
        message="QUARANTINE",
        correlation_key="breaker:Drawdown",
        ts=now,
    )
    await log.write(a)

    results = await log.read_since(now - timedelta(seconds=10))
    assert len(results) == 1
    got = results[0]
    assert got.level == a.level
    assert got.component == a.component
    assert got.message == a.message
    assert got.correlation_key == a.correlation_key
    assert got.timestamp == a.timestamp
    await log.stop()


async def test_correlation_key_can_be_null(tmp_path: Path) -> None:
    log = SQLiteAlertLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    await log.write(_alert(correlation_key=None, ts=now))

    results = await log.read_since(now - timedelta(seconds=10))
    assert len(results) == 1
    assert results[0].correlation_key is None
    await log.stop()


async def test_filter_by_level(tmp_path: Path) -> None:
    log = SQLiteAlertLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    await log.write(_alert(level=AlertLevel.INFO, ts=now))
    await log.write(_alert(level=AlertLevel.CRITICAL, ts=now))

    crit = await log.read_since(now - timedelta(seconds=10), level=AlertLevel.CRITICAL)
    assert len(crit) == 1
    assert crit[0].level == AlertLevel.CRITICAL
    await log.stop()


async def test_filter_by_component(tmp_path: Path) -> None:
    log = SQLiteAlertLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    await log.write(_alert(component="OMS", ts=now))
    await log.write(_alert(component="PMS", ts=now))

    oms = await log.read_since(now - timedelta(seconds=10), component="OMS")
    assert len(oms) == 1
    assert oms[0].component == "OMS"
    await log.stop()


async def test_filter_by_level_and_component_combined(tmp_path: Path) -> None:
    log = SQLiteAlertLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    await log.write(_alert(level=AlertLevel.INFO, component="OMS", ts=now))
    await log.write(_alert(level=AlertLevel.WARN, component="OMS", ts=now))
    await log.write(_alert(level=AlertLevel.WARN, component="PMS", ts=now))

    result = await log.read_since(
        now - timedelta(seconds=10),
        level=AlertLevel.WARN,
        component="OMS",
    )
    assert len(result) == 1
    assert result[0].level == AlertLevel.WARN
    assert result[0].component == "OMS"
    await log.stop()


async def test_results_ordered_by_timestamp_ascending(tmp_path: Path) -> None:
    log = SQLiteAlertLog(tmp_path / "test.db")
    await log.start()
    base = datetime.now(timezone.utc)
    await log.write(_alert(message="last", ts=base + timedelta(seconds=10)))
    await log.write(_alert(message="first", ts=base))
    await log.write(_alert(message="middle", ts=base + timedelta(seconds=5)))

    results = await log.read_since(base - timedelta(seconds=10))
    assert [r.message for r in results] == ["first", "middle", "last"]
    await log.stop()


async def test_durability_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "durability.db"
    now = datetime.now(timezone.utc)

    log1 = SQLiteAlertLog(db_path)
    await log1.start()
    await log1.write(_alert(level=AlertLevel.CRITICAL, message="boom", ts=now))
    await log1.stop()

    log2 = SQLiteAlertLog(db_path)
    await log2.start()
    results = await log2.read_since(now - timedelta(seconds=10))
    assert len(results) == 1
    assert results[0].message == "boom"
    await log2.stop()


async def test_empty_returns_empty_list(tmp_path: Path) -> None:
    log = SQLiteAlertLog(tmp_path / "test.db")
    await log.start()
    results = await log.read_since(datetime.now(timezone.utc))
    assert results == []
    await log.stop()
