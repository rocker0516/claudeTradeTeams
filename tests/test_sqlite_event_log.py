"""SQLiteEventLog contract tests — generic dataclass round-trip.

Exercises the dataclass <-> JSON serialization in `_serde.py` via real
runtime event types (mix of str-Enum / datetime / int / str / Optional).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest

from trading.domain import TriggerAction
from trading.persistence.event_log import SQLiteEventLog
from trading.runtime import (
    BreakerTripped,
    Heartbeat,
    ReconciliationCompleted,
    SystemState,
    SystemStateChanged,
)

if TYPE_CHECKING:
    from pathlib import Path


async def test_system_state_changed_round_trip(tmp_path: Path) -> None:
    log = SQLiteEventLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    ev = SystemStateChanged(
        old_state=SystemState.RUNNING,
        new_state=SystemState.QUARANTINE,
        reason="dd > 5%",
        timestamp=now,
    )
    await log.write(ev)

    results = await log.read_since(SystemStateChanged, now - timedelta(seconds=10))
    assert len(results) == 1
    got = results[0]
    assert got.old_state == ev.old_state
    assert got.new_state == ev.new_state
    assert got.reason == ev.reason
    assert got.timestamp == ev.timestamp
    await log.stop()


async def test_breaker_tripped_round_trip_with_trigger_action_enum(
    tmp_path: Path,
) -> None:
    log = SQLiteEventLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    ev = BreakerTripped(
        breaker_name="DrawdownBreaker",
        action=TriggerAction.FLATTEN,
        reason="dd 12%",
        timestamp=now,
    )
    await log.write(ev)

    results = await log.read_since(BreakerTripped, now - timedelta(seconds=10))
    assert len(results) == 1
    got = results[0]
    assert got.breaker_name == ev.breaker_name
    assert got.action == TriggerAction.FLATTEN  # enum preserved
    assert got.reason == ev.reason
    await log.stop()


async def test_filter_by_type_excludes_others(tmp_path: Path) -> None:
    log = SQLiteEventLog(tmp_path / "test.db")
    await log.start()
    now = datetime.now(timezone.utc)
    await log.write(SystemStateChanged(SystemState.BOOTING, SystemState.RUNNING, "boot", now))
    await log.write(Heartbeat(component="Supervisor", timestamp=now))

    # Asking for Heartbeat only returns Heartbeat
    hbs = await log.read_since(Heartbeat, now - timedelta(seconds=10))
    assert len(hbs) == 1
    assert hbs[0].component == "Supervisor"
    # Asking for SystemStateChanged only returns it
    scs = await log.read_since(SystemStateChanged, now - timedelta(seconds=10))
    assert len(scs) == 1
    await log.stop()


async def test_filter_by_time_excludes_old(tmp_path: Path) -> None:
    log = SQLiteEventLog(tmp_path / "test.db")
    await log.start()
    base = datetime.now(timezone.utc)
    await log.write(Heartbeat(component="X", timestamp=base - timedelta(hours=2)))
    await log.write(Heartbeat(component="X", timestamp=base))

    recent = await log.read_since(Heartbeat, base - timedelta(minutes=30))
    assert len(recent) == 1
    await log.stop()


async def test_results_ordered_by_timestamp_ascending(tmp_path: Path) -> None:
    log = SQLiteEventLog(tmp_path / "test.db")
    await log.start()
    base = datetime.now(timezone.utc)
    # Insert out of order
    await log.write(Heartbeat(component="X", timestamp=base + timedelta(seconds=10)))
    await log.write(Heartbeat(component="X", timestamp=base + timedelta(seconds=5)))
    await log.write(Heartbeat(component="X", timestamp=base))

    results = await log.read_since(Heartbeat, base - timedelta(minutes=1))
    timestamps = [r.timestamp for r in results]
    assert timestamps == sorted(timestamps)
    await log.stop()


async def test_event_without_timestamp_rejected(tmp_path: Path) -> None:
    log = SQLiteEventLog(tmp_path / "test.db")
    await log.start()
    with pytest.raises(TypeError, match="timestamp"):
        await log.write(object())
    await log.stop()


async def test_durability_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "durability.db"
    now = datetime.now(timezone.utc)

    log1 = SQLiteEventLog(db_path)
    await log1.start()
    await log1.write(ReconciliationCompleted(component="OMS", drift_count=2, timestamp=now))
    await log1.stop()

    log2 = SQLiteEventLog(db_path)
    await log2.start()
    results = await log2.read_since(ReconciliationCompleted, now - timedelta(seconds=10))
    assert len(results) == 1
    assert results[0].drift_count == 2
    await log2.stop()
