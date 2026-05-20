"""Entrypoint tests.

Covers `trading.runtime.main:build_supervisor` (the composition root)
and `_parse_args` (the CLI surface). The full `run()` involves signal
handlers and `asyncio.run`, which we don't exercise here — instead we
verify the assembled supervisor can boot and shut down cleanly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from trading.persistence.event_log import SQLiteEventLog
from trading.runtime import SystemState, SystemStateChanged
from trading.runtime.main import _parse_args, build_supervisor

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_args_default_values() -> None:
    args = _parse_args([])
    assert args.tick_interval == 1.0
    assert args.log_level == "INFO"
    assert args.db_path == "trading.db"


def test_parse_args_custom_tick_interval() -> None:
    args = _parse_args(["--tick-interval", "0.25"])
    assert args.tick_interval == 0.25


def test_parse_args_custom_db_path() -> None:
    args = _parse_args(["--db-path", "/var/lib/trading/state.db"])
    assert args.db_path == "/var/lib/trading/state.db"


def test_parse_args_memory_db_path() -> None:
    args = _parse_args(["--db-path", ":memory:"])
    assert args.db_path == ":memory:"


def test_parse_args_log_level_validates_choice() -> None:
    args = _parse_args(["--log-level", "DEBUG"])
    assert args.log_level == "DEBUG"


def test_build_supervisor_returns_in_booting_state() -> None:
    sup = build_supervisor(tick_interval=0.04)
    assert sup.state == SystemState.BOOTING


async def test_built_supervisor_boots_and_shuts_down() -> None:
    """End-to-end: the composition root produces a runnable system."""
    sup = build_supervisor(tick_interval=0.04)

    await sup.start()
    assert sup.state == SystemState.RUNNING

    # Let it idle for a few ticks (heartbeat publishes, PMS reconciles,
    # Risk ticks) — verifies tick loop doesn't crash
    await asyncio.sleep(0.15)
    assert sup.state == SystemState.RUNNING

    await sup.stop(drain=True)
    assert sup.state == SystemState.STOPPED


async def test_built_supervisor_handles_quarantine_path() -> None:
    """Smoke test the QUARANTINE machinery is wired correctly."""
    sup = build_supervisor(tick_interval=0.04)
    await sup.start()
    await sup.quarantine("manual smoke test")
    assert sup.state == SystemState.QUARANTINE
    await sup.acknowledge()
    assert sup.state == SystemState.RUNNING
    await sup.stop()


async def test_built_supervisor_with_file_db_survives_restart(
    tmp_path: Path,
) -> None:
    """The headline durability test for the entrypoint composition.

    1. Build supervisor with a real SQLite file
    2. Start (transitions BOOTING -> RECONCILING -> RUNNING — each
       persisted to event log via the SystemStateChanged event flow)
    3. Stop (transitions to DRAINING -> STOPPED)
    4. Open a fresh SQLiteEventLog reading the same file → state-change
       events are still there
    """
    db_path = tmp_path / "entrypoint.db"
    start_time = datetime.now(timezone.utc)

    sup = build_supervisor(tick_interval=0.04, db_path=db_path)
    await sup.start()
    await asyncio.sleep(0.05)
    await sup.stop()

    # Re-open the same DB through a fresh journal and verify history
    log = SQLiteEventLog(db_path)
    await log.start()
    try:
        transitions = await log.read_since(SystemStateChanged, start_time - timedelta(seconds=10))
    finally:
        await log.stop()

    # Expect at least:
    #   BOOTING -> RECONCILING -> RUNNING -> DRAINING -> STOPPED
    assert len(transitions) >= 4
    assert transitions[0].old_state == SystemState.BOOTING
    assert transitions[-1].new_state == SystemState.STOPPED
