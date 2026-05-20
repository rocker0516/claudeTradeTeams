"""Production entrypoint for the trading system.

Wires Default* components into a DefaultSupervisor, sets up logging,
runs the lifecycle, and handles SIGTERM / SIGINT for graceful shutdown.

For Phase 0 this is a single-mode entrypoint: the full Default stack
(DefaultSupervisor + DefaultOMS + DefaultPMS + DefaultRiskManager with
DrawdownBreaker + HeartbeatBreaker) wired with Null* for venue, market
data, and strategy. The system boots, idles in RUNNING (emitting
heartbeats), and shuts down cleanly on signal. No real trading happens
— that requires BybitVenue + LiveMarketDataSource which are post-Phase 0.

Persistence uses SQLite (`--db-path`, default ./trading.db). All 4
journals share the same file — restart preserves order history, fills,
position snapshots, event audit log, and alert log.

USAGE
    python -m trading                                # ./trading.db, tick 1s
    python -m trading --tick-interval 0.5
    python -m trading --db-path /var/lib/trading/state.db
    python -m trading --db-path :memory:             # ephemeral, no durability
    python -m trading --log-level DEBUG

SIGNALS
    SIGINT  (Ctrl-C)   → graceful drain + shutdown
    SIGTERM            → same as SIGINT

PLATFORM NOTES
    On Linux, `loop.add_signal_handler` catches both SIGTERM and SIGINT
    cleanly into the event loop. On Windows, asyncio doesn't support
    `add_signal_handler` for SIGTERM (Windows doesn't really have it),
    and Ctrl-C raises KeyboardInterrupt which propagates as task
    cancellation — the `finally` cleanup still runs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from ..alerts.channels import LogChannel
from ..alerts.router import InMemoryAlertRouter
from ..clock import RealClock
from ..domain import AlertLevel
from ..executor import DefaultExecutor
from ..market_data import NullMarketDataSource
from ..oms import DefaultOrderManager
from ..persistence.account_journal import SQLiteAccountJournal
from ..persistence.alert_log import SQLiteAlertLog
from ..persistence.event_log import SQLiteEventLog
from ..persistence.order_journal import SQLiteOrderJournal
from ..pms import DefaultPositionManager
from ..risk import DefaultRiskManager
from ..risk.breakers import DrawdownBreaker, HeartbeatBreaker
from ..runtime.event_bus import InMemoryEventBus
from ..runtime.supervisor import DefaultSupervisor
from ..strategy import NullStrategy
from ..venues import NullVenue

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)


def build_supervisor(
    *,
    tick_interval: float = 1.0,
    db_path: str | Path = ":memory:",
) -> DefaultSupervisor:
    """Composition root for the Phase 0 idle entrypoint.

    Wires every Default* component with Null* placeholders for venue,
    market data, and strategy. The result is a fully runnable
    DefaultSupervisor that boots, idles, and shuts down cleanly — but
    doesn't trade (NullVenue refuses orders, NullStrategy never submits).

    Persistence uses SQLite. The four journals (orders, accounts,
    events, alerts) share the same DB file. Default `:memory:` is
    intended for tests — each journal opens its own connection so the
    4 in-memory DBs are isolated, but data is lost on stop. Production
    callers pass a real file path.

    Exposed as a function (not just inline in `_main_async`) so tests
    can call it directly and assert lifecycle behavior without spawning
    a process.
    """
    clock = RealClock()
    bus = InMemoryEventBus()
    venue = NullVenue(clock)
    market_data = NullMarketDataSource()

    order_journal = SQLiteOrderJournal(db_path)
    account_journal = SQLiteAccountJournal(db_path)
    event_log = SQLiteEventLog(db_path)
    alert_log = SQLiteAlertLog(db_path)

    oms = DefaultOrderManager(venue=venue, journal=order_journal, clock=clock)
    pms = DefaultPositionManager(venue=venue, clock=clock, journal=account_journal)

    breakers = [
        DrawdownBreaker(
            bus=bus,
            threshold=Decimal("0.05"),
            window=timedelta(hours=24),
        ),
        HeartbeatBreaker(
            bus=bus,
            components=["Supervisor"],
            threshold=timedelta(seconds=10),
        ),
    ]
    risk = DefaultRiskManager(clock=clock, bus=bus, oms=oms, pms=pms, breakers=breakers)
    executor = DefaultExecutor(risk=risk, orders=oms, positions=pms)

    alert_router = InMemoryAlertRouter()
    alert_router.register(
        LogChannel(),
        levels=(
            AlertLevel.INFO,
            AlertLevel.WARN,
            AlertLevel.ALERT,
            AlertLevel.CRITICAL,
        ),
    )

    return DefaultSupervisor(
        clock=clock,
        event_bus=bus,
        venue=venue,
        market_data=market_data,
        oms=oms,
        pms=pms,
        risk=risk,
        executor=executor,
        strategy=NullStrategy(),
        alert_router=alert_router,
        order_journal=order_journal,
        account_journal=account_journal,
        event_log=event_log,
        alert_log=alert_log,
        risk_tick_interval=tick_interval,
    )


async def _main_async(args: argparse.Namespace) -> None:
    supervisor = build_supervisor(
        tick_interval=args.tick_interval,
        db_path=args.db_path,
    )
    stop_event = asyncio.Event()

    # Wire signals into the event loop where supported
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
            _logger.debug("Installed signal handler for %s", sig.name)
        except NotImplementedError:
            # Windows: asyncio doesn't support add_signal_handler.
            # Ctrl-C still works via KeyboardInterrupt → task cancellation.
            _logger.debug(
                "Signal %s not installable via asyncio (likely Windows); "
                "relying on KeyboardInterrupt",
                sig.name,
            )

    _logger.info(
        "Booting supervisor (tick_interval=%.2fs, db_path=%s)",
        args.tick_interval,
        args.db_path,
    )
    await supervisor.start()
    _logger.info(
        "Running. State=%s. Send SIGTERM or Ctrl-C to stop.",
        supervisor.state.name,
    )

    try:
        await stop_event.wait()
    finally:
        _logger.info("Initiating graceful shutdown")
        try:
            await supervisor.stop(drain=True)
        except Exception:
            _logger.exception("Error during supervisor.stop")
        _logger.info("Stopped. Final state=%s", supervisor.state.name)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trading",
        description="Phase 0 idle entrypoint — Default stack with Null venue / data / strategy",
    )
    parser.add_argument(
        "--tick-interval",
        type=float,
        default=1.0,
        help="Risk tick + PMS reconcile + heartbeat publish interval, seconds (default: 1.0)",
    )
    parser.add_argument(
        "--db-path",
        default="trading.db",
        help=(
            "SQLite database file path (default: ./trading.db). "
            "Use ':memory:' for ephemeral (no durability)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(_main_async(args))
        return 0
    except KeyboardInterrupt:
        # On Windows the loop's cleanup may bypass — log and exit 130
        # (conventional "interrupted" exit code).
        _logger.warning("Interrupted before clean shutdown; some state may be incomplete")
        return 130


if __name__ == "__main__":
    sys.exit(run())
