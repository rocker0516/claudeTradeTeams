"""DefaultSupervisor — minimal lifecycle orchestrator.

Wires the component graph, owns the SystemState machine, and
handles BreakerTripped dispatch. Reconciliation is a stub for now
(calls Venue.get_open_orders / get_positions but does nothing with
the result) — full reconciliation logic comes when OMS / PMS have
real implementations.

The Strategy is NOT wired by Supervisor — strategies set up their
own market-data subscriptions in __init__ (they receive their
dependencies via composition root).
"""

import asyncio
import contextlib
import logging

from ...alerts import AlertRouter
from ...clock import Clock
from ...domain import Alert, AlertLevel, TriggerAction
from ...executor import Executor
from ...market_data import MarketDataSource
from ...oms import OrderManager
from ...persistence import AccountJournal, AlertLog, EventLog, OrderJournal
from ...pms import PositionManager
from ...risk import RiskManager
from ...strategy import Strategy
from ...venues import Venue
from ..event_bus import EventBus
from ..events import BreakerTripped, Heartbeat, SystemStateChanged
from ..lifecycle import Lifecycle
from ..system_state import SystemState
from .base import Supervisor

_logger = logging.getLogger(__name__)


class DefaultSupervisor(Supervisor):
    def __init__(
        self,
        *,
        clock: Clock,
        event_bus: EventBus,
        venue: Venue,
        market_data: MarketDataSource,
        oms: OrderManager,
        pms: PositionManager,
        risk: RiskManager,
        executor: Executor,
        strategy: Strategy,
        alert_router: AlertRouter,
        order_journal: OrderJournal,
        account_journal: AccountJournal,
        event_log: EventLog,
        alert_log: AlertLog,
        risk_tick_interval: float = 1.0,
    ) -> None:
        self._clock = clock
        self._bus = event_bus
        self._venue = venue
        self._market_data = market_data
        self._oms = oms
        self._pms = pms
        self._risk = risk
        self._executor = executor
        self._strategy = strategy
        self._alert_router = alert_router
        self._order_journal = order_journal
        self._account_journal = account_journal
        self._event_log = event_log
        self._alert_log = alert_log
        self._risk_tick_interval = risk_tick_interval

        self._state = SystemState.BOOTING
        self._bus_running = False
        self._tick_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> SystemState:
        return self._state

    async def _transition(self, new_state: SystemState, reason: str) -> None:
        old = self._state
        event = SystemStateChanged(
            old_state=old,
            new_state=new_state,
            reason=reason,
            timestamp=self._clock.now(),
        )
        self._state = new_state
        await self._event_log.write(event)
        if self._bus_running:
            await self._bus.publish(event)

    async def start(self) -> None:
        if self._state != SystemState.BOOTING:
            raise RuntimeError(f"start() requires BOOTING, currently {self._state.name}")

        # 1. Persistence first — we need to log state changes from here on
        await self._order_journal.start()
        await self._account_journal.start()
        await self._event_log.start()
        await self._alert_log.start()

        # 2. Alerts (needed to surface any boot failures)
        await self._alert_router.start()

        # 3. Bus + subscribe to BreakerTripped before venue can emit events
        await self._bus.start()
        self._bus_running = True
        self._bus.subscribe(BreakerTripped, self._on_breaker_tripped)

        await self._transition(SystemState.RECONCILING, "components started")

        # 4. Restore OMS / PMS caches from journals (Lifecycle-conditional —
        # NullOMS / NullPMS don't implement start/stop, isinstance() skips).
        # Restore runs before venue reconcile so the cache has last-known
        # state available if the first venue call fails.
        for restorable in (self._oms, self._pms):
            if isinstance(restorable, Lifecycle):
                await restorable.start()

        # 5. Venue + market data
        await self._venue.start()
        await self._market_data.start()

        # 6. Initial reconciliation (stub — exercises the API but ignores results)
        _ = await self._venue.get_open_orders()
        _ = await self._venue.get_positions()
        _ = await self._venue.get_balance()
        await self._pms.reconcile()

        await self._transition(SystemState.RUNNING, "reconciliation complete")

        # 6. Start periodic risk tick + heartbeat loop
        self._tick_task = asyncio.create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        """Periodic loop: emit own heartbeat, then call risk.tick().

        Exceptions from heartbeat publish or risk.tick() are caught and
        logged so a transient failure doesn't kill the loop. CancelledError
        propagates to terminate the loop cleanly on shutdown.
        """
        while True:
            try:
                await self._clock.sleep(self._risk_tick_interval)
            except asyncio.CancelledError:
                return

            try:
                await self._bus.publish(
                    Heartbeat(component="Supervisor", timestamp=self._clock.now())
                )
            except Exception:
                _logger.exception("supervisor heartbeat publish failed")

            try:
                await self._pms.reconcile()
            except Exception:
                _logger.exception("pms reconcile failed")

            try:
                await self._risk.tick()
            except Exception:
                _logger.exception("risk tick failed")

    async def stop(self, *, drain: bool = True) -> None:
        if self._state in (SystemState.STOPPED, SystemState.DRAINING):
            return

        # Cancel tick task first — no more new BreakerTripped events
        if self._tick_task is not None:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None

        await self._transition(SystemState.DRAINING, f"shutdown drain={drain}")

        if drain:
            try:
                await self._oms.cancel_all()
            except Exception:
                _logger.exception("cancel_all during drain failed; continuing shutdown")

        # Stop everything in reverse order. Any individual failure must
        # not block other components from stopping.
        external: tuple[Lifecycle, ...] = (self._market_data, self._venue)
        for component in external:
            await self._safe_stop(component)

        # Take bus offline before persistence so late publishes don't reach
        # subscribers after their stop()
        self._bus_running = False
        await self._safe_stop(self._bus)

        # Stop OMS / PMS (Lifecycle-conditional — same gate as start)
        for restorable in (self._oms, self._pms):
            if isinstance(restorable, Lifecycle):
                await self._safe_stop(restorable)

        # Log the final state transition while event_log is still up.
        # bus_running is False so this only goes to the journal, not the bus.
        await self._transition(SystemState.STOPPED, "shutdown complete")

        persistence: tuple[Lifecycle, ...] = (
            self._alert_router,
            self._alert_log,
            self._event_log,
            self._account_journal,
            self._order_journal,
        )
        for component in persistence:
            await self._safe_stop(component)

        # _state is already SystemState.STOPPED from the _transition call above

    @staticmethod
    async def _safe_stop(component: Lifecycle) -> None:
        """Stop a Lifecycle component; log and swallow any exception so
        one bad component doesn't block the others from shutting down."""
        try:
            await component.stop()
        except Exception:
            _logger.exception("error stopping %s", type(component).__name__)

    async def quarantine(
        self,
        reason: str,
        *,
        breaker_name: str | None = None,
    ) -> None:
        if self._state != SystemState.RUNNING:
            _logger.warning(
                "quarantine() ignored — state is %s, not RUNNING",
                self._state.name,
            )
            return

        try:
            await self._oms.cancel_all()
        except Exception:
            _logger.exception("cancel_all during quarantine failed")

        await self._transition(SystemState.QUARANTINE, reason)

        await self._alert_router.alert(
            Alert(
                level=AlertLevel.CRITICAL,
                component="Supervisor",
                message=f"QUARANTINE: {reason}"
                + (f" (breaker={breaker_name})" if breaker_name else ""),
                timestamp=self._clock.now(),
                correlation_key=f"quarantine:{breaker_name or 'manual'}",
            )
        )

    async def acknowledge(self) -> None:
        if self._state != SystemState.QUARANTINE:
            raise RuntimeError(
                f"acknowledge() only valid in QUARANTINE, currently {self._state.name}"
            )

        await self._transition(SystemState.RECONCILING, "human ack")
        # Re-reconcile (stub for now)
        _ = await self._venue.get_open_orders()
        _ = await self._venue.get_positions()
        await self._pms.reconcile()
        await self._transition(SystemState.RUNNING, "reconciliation after ack OK")

    async def _on_breaker_tripped(self, event: BreakerTripped) -> None:
        """EventBus subscriber for BreakerTripped — dispatches on action."""
        match event.action:
            case TriggerAction.REJECT_NEW:
                # Risk gate already enforces this on submit; nothing extra here.
                pass
            case TriggerAction.CANCEL_ALL:
                try:
                    await self._oms.cancel_all()
                except Exception:
                    _logger.exception("cancel_all from BreakerTripped failed")
            case TriggerAction.QUARANTINE:
                await self.quarantine(event.reason, breaker_name=event.breaker_name)
            case TriggerAction.FLATTEN:
                try:
                    await self._oms.cancel_all()
                except Exception:
                    _logger.exception("cancel_all from FLATTEN breaker failed")
                # TODO: actual flatten — submit reduce_only orders for every position.
                # For now, treat as QUARANTINE (positions remain open, human ack required).
                await self.quarantine(event.reason, breaker_name=event.breaker_name)
            case _:
                raise TypeError(f"unknown TriggerAction: {event.action}")
