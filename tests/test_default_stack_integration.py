"""Full Default stack integration tests.

Wires DefaultOrderManager + DefaultPositionManager + DefaultRiskManager
+ DrawdownBreaker (latching) + DefaultSupervisor + in-memory persistence
layer + LogChannel alerts. Verifies the production-target wiring works
as a cohesive unit end-to-end.

Prior integration tests (test_default_supervisor.py, test_supervisor_
heartbeat_integration.py) used Null* for OMS/PMS/Risk to isolate
Supervisor. These tests verify the Default versions don't break the
chain when wired together.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading.alerts.channels import LogChannel
from trading.alerts.router import InMemoryAlertRouter
from trading.clock import RealClock
from trading.domain import (
    AlertLevel,
    Balance,
    Fill,
    Order,
    OrderId,
    OrderRequest,
    OrderSide,
    OrderType,
    Position,
    RiskRejected,
    Submitted,
    Symbol,
    TimeInForce,
    TradeIntent,
)
from trading.executor import DefaultExecutor
from trading.market_data import NullMarketDataSource
from trading.oms import DefaultOrderManager
from trading.persistence.account_journal import InMemoryAccountJournal
from trading.persistence.alert_log import InMemoryAlertLog
from trading.persistence.event_log import InMemoryEventLog
from trading.persistence.order_journal import InMemoryOrderJournal
from trading.pms import DefaultPositionManager
from trading.risk import DefaultRiskManager
from trading.risk.breakers import DrawdownBreaker
from trading.runtime import SystemState
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.supervisor import DefaultSupervisor
from trading.strategy import NullStrategy
from trading.venues import Venue


class _DynamicVenue(Venue):
    """Venue stub with mutable balance/positions for integration tests."""

    def __init__(
        self,
        clock: RealClock,
        *,
        initial_balance: Decimal = Decimal("1000"),
    ) -> None:
        self._clock = clock
        self._next_oid = 0
        self._positions: list[Position] = []
        self._balance_total = initial_balance
        self._order_cbs: list[Callable[[Order], Awaitable[None]]] = []
        self._fill_cbs: list[Callable[[Fill], Awaitable[None]]] = []
        self.placed: list[OrderRequest] = []
        self.cancelled: list[OrderId] = []

    def set_balance(self, total: Decimal) -> None:
        self._balance_total = total

    def set_positions(self, positions: list[Position]) -> None:
        self._positions = list(positions)

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def place_order(self, request: OrderRequest) -> OrderId:
        self._next_oid += 1
        oid = OrderId(f"v-{self._next_oid}")
        self.placed.append(request)
        return oid

    async def cancel_order(self, order_id: OrderId) -> bool:
        self.cancelled.append(order_id)
        return True

    async def get_order(self, order_id: OrderId) -> Order | None:
        return None

    async def get_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        return []

    async def get_positions(self) -> list[Position]:
        return list(self._positions)

    async def get_balance(self) -> Balance:
        return Balance(
            currency="USDT",
            total=self._balance_total,
            available=self._balance_total,
            margin_used=Decimal("0"),
            updated_at=self._clock.now(),
        )

    def on_order_update(self, callback: Callable[[Order], Awaitable[None]]) -> None:
        self._order_cbs.append(callback)

    def on_fill(self, callback: Callable[[Fill], Awaitable[None]]) -> None:
        self._fill_cbs.append(callback)


@dataclass
class _Stack:
    """Bag of references the test functions need direct access to."""

    supervisor: DefaultSupervisor
    bus: InMemoryEventBus
    venue: _DynamicVenue
    executor: DefaultExecutor
    pms: DefaultPositionManager
    order_journal: InMemoryOrderJournal
    account_journal: InMemoryAccountJournal
    clock: RealClock


def _build(
    *,
    drawdown_threshold: str = "0.05",
    initial_balance: str = "1000",
    tick_interval: float = 0.04,
) -> _Stack:
    clock = RealClock()
    bus = InMemoryEventBus()
    venue = _DynamicVenue(clock, initial_balance=Decimal(initial_balance))

    order_journal = InMemoryOrderJournal()
    account_journal = InMemoryAccountJournal()
    event_log = InMemoryEventLog()
    alert_log = InMemoryAlertLog()

    oms = DefaultOrderManager(venue=venue, journal=order_journal, clock=clock)
    pms = DefaultPositionManager(venue=venue, clock=clock, journal=account_journal)

    drawdown = DrawdownBreaker(
        bus=bus,
        threshold=Decimal(drawdown_threshold),
        window=timedelta(seconds=60),
    )
    risk = DefaultRiskManager(clock=clock, bus=bus, oms=oms, pms=pms, breakers=[drawdown])
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

    supervisor = DefaultSupervisor(
        clock=clock,
        event_bus=bus,
        venue=venue,
        market_data=NullMarketDataSource(),
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

    return _Stack(
        supervisor=supervisor,
        bus=bus,
        venue=venue,
        executor=executor,
        pms=pms,
        order_journal=order_journal,
        account_journal=account_journal,
        clock=clock,
    )


def _intent(quantity: str = "1") -> TradeIntent:
    return TradeIntent(
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        price=Decimal("60000"),
        time_in_force=TimeInForce.GTC,
    )


# ============================================================
# Boot / shutdown
# ============================================================


async def test_default_stack_boots_to_running() -> None:
    stack = _build()
    await stack.supervisor.start()
    assert stack.supervisor.state == SystemState.RUNNING
    await stack.supervisor.stop()


async def test_default_stack_shuts_down_cleanly() -> None:
    stack = _build()
    await stack.supervisor.start()
    await stack.supervisor.stop()
    assert stack.supervisor.state == SystemState.STOPPED


# ============================================================
# Tick loop wires PMS reconciliation
# ============================================================


async def test_pms_reconciles_balance_into_journal_each_tick() -> None:
    """Supervisor's tick loop calls pms.reconcile() -> balance snapshots appear."""
    stack = _build(tick_interval=0.04)
    await stack.supervisor.start()

    # Wait for several tick intervals
    await asyncio.sleep(0.2)
    await stack.supervisor.stop()

    # Latest balance snapshot should match what we set in venue
    latest = await stack.account_journal.read_latest_balance()
    assert latest is not None
    assert latest.total == Decimal("1000")


async def test_pms_reflects_venue_balance_changes_after_tick() -> None:
    """Change venue balance -> next tick reconciles -> PMS reflects."""
    stack = _build(tick_interval=0.04)
    await stack.supervisor.start()
    await asyncio.sleep(0.05)  # let initial reconcile settle

    # Change venue balance
    stack.venue.set_balance(Decimal("750"))
    await asyncio.sleep(0.15)  # wait for several ticks

    bal = await stack.pms.balance()
    assert bal.total == Decimal("750")
    await stack.supervisor.stop()


# ============================================================
# Drawdown triggers QUARANTINE through the full chain
# ============================================================


async def test_drawdown_chain_triggers_quarantine() -> None:
    """The big one: balance drop -> PMS -> Risk -> DrawdownBreaker -> Supervisor."""
    stack = _build(drawdown_threshold="0.05", initial_balance="1000", tick_interval=0.04)
    await stack.supervisor.start()
    await asyncio.sleep(0.05)  # let initial reconcile + first tick record peak

    # Drop balance 10% (above 5% threshold)
    stack.venue.set_balance(Decimal("900"))
    # Wait long enough for: PMS reconcile + Risk tick + BreakerTripped dispatch
    await asyncio.sleep(0.2)

    assert stack.supervisor.state == SystemState.QUARANTINE
    await stack.supervisor.stop()


async def test_minor_drawdown_does_not_quarantine() -> None:
    """3% drop with 5% threshold should leave state RUNNING."""
    stack = _build(drawdown_threshold="0.05", initial_balance="1000", tick_interval=0.04)
    await stack.supervisor.start()
    await asyncio.sleep(0.05)

    stack.venue.set_balance(Decimal("970"))  # 3% drop
    await asyncio.sleep(0.2)

    assert stack.supervisor.state == SystemState.RUNNING
    await stack.supervisor.stop()


# ============================================================
# Trade submission through the Executor facade
# ============================================================


async def test_submit_intent_approved_path_reaches_venue() -> None:
    """Strategy -> Executor -> Risk(approve) -> OMS -> Venue.place_order."""
    stack = _build()
    await stack.supervisor.start()

    result = await stack.executor.submit_intent(_intent(quantity="1"))

    assert isinstance(result, Submitted)
    assert result.order_id == OrderId("v-1")
    assert len(stack.venue.placed) == 1
    assert stack.venue.placed[0].symbol == Symbol("BTCUSDT")
    # OMS also persisted the SUBMITTED-state order
    open_orders = await stack.order_journal.read_open_orders()
    assert len(open_orders) == 1

    await stack.supervisor.stop()


async def test_submit_intent_rejected_when_drawdown_latched() -> None:
    """After drawdown trips, Risk rejects further intents until ack."""
    stack = _build(drawdown_threshold="0.05", initial_balance="1000", tick_interval=0.04)
    await stack.supervisor.start()
    await asyncio.sleep(0.05)

    # Trip drawdown
    stack.venue.set_balance(Decimal("900"))
    await asyncio.sleep(0.2)
    assert stack.supervisor.state == SystemState.QUARANTINE

    # Now try to submit — Risk should reject because breaker is latched
    result = await stack.executor.submit_intent(_intent())
    assert isinstance(result, RiskRejected)
    assert "DrawdownBreaker" in (result.breaker_name or "")
    # Venue never saw the order
    assert stack.venue.placed == []

    await stack.supervisor.stop()


# ============================================================
# Acknowledge un-latches via SystemStateChanged event
# ============================================================


async def test_acknowledge_clears_drawdown_latch_and_returns_to_running() -> None:
    stack = _build(drawdown_threshold="0.05", initial_balance="1000", tick_interval=0.04)
    await stack.supervisor.start()
    await asyncio.sleep(0.05)

    # Trip
    stack.venue.set_balance(Decimal("900"))
    await asyncio.sleep(0.2)
    assert stack.supervisor.state == SystemState.QUARANTINE

    # Restore the balance (otherwise breaker re-trips immediately since
    # baseline resets to current low and any further drop = re-trip — but
    # not dropping further means no trip, which is also a valid path)
    stack.venue.set_balance(Decimal("1000"))

    await stack.supervisor.acknowledge()

    # Right after acknowledge, supervisor is back in RUNNING
    assert stack.supervisor.state == SystemState.RUNNING

    # And further submits go through (breaker is unlatched)
    result = await stack.executor.submit_intent(_intent())
    assert isinstance(result, Submitted)

    await stack.supervisor.stop()


async def test_acknowledge_outside_quarantine_raises() -> None:
    stack = _build()
    await stack.supervisor.start()
    with pytest.raises(RuntimeError, match="QUARANTINE"):
        await stack.supervisor.acknowledge()
    await stack.supervisor.stop()


# ============================================================
# Sanity: nothing crashes during a sustained idle period
# ============================================================


async def test_default_stack_survives_sustained_idle() -> None:
    """Run for many tick intervals with no events; verify still RUNNING."""
    stack = _build(tick_interval=0.02)  # tight interval
    await stack.supervisor.start()

    await asyncio.sleep(0.4)  # ~20 ticks

    assert stack.supervisor.state == SystemState.RUNNING
    # Position snapshots accumulating in journal — verify reconciliation
    # actually ran multiple times
    base = datetime.now(timezone.utc) - timedelta(seconds=10)
    history = await stack.account_journal.read_position_history(Symbol("BTCUSDT"), base)
    # No positions configured, so history is empty — that's fine. We only
    # verify the *balance* writes happened by checking latest balance.
    _ = history
    latest_bal = await stack.account_journal.read_latest_balance()
    assert latest_bal is not None

    await stack.supervisor.stop()
