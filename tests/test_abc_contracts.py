"""Phase 0 ABC contract tests.

Verifies the contracts in src/trading/. No concrete impls exist yet —
we test that abstract classes refuse instantiation, that ADTs behave
correctly, and that small in-memory fakes can satisfy the contracts.
"""

import contextlib
from abc import ABC
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TypeVar

import pytest

from trading.alerts import AlertChannel, AlertRouter
from trading.clock import Clock
from trading.domain import (
    Alert,
    AlertLevel,
    Approved,
    ClientOrderId,
    Decision,
    Fill,
    Order,
    OrderId,
    OrderSide,
    OrderStatus,
    OrderType,
    Rejected,
    RiskRejected,
    SubmissionFailed,
    SubmitResult,
    Submitted,
    Symbol,
    TimeInForce,
    Trigger,
    TriggerAction,
    VenueRejected,
)
from trading.executor import Executor
from trading.fill import FillEngine
from trading.market_data import MarketDataSource
from trading.oms import OrderManager
from trading.persistence import AccountJournal, AlertLog, EventLog, OrderJournal
from trading.pms import PositionManager
from trading.risk import CircuitBreaker, RiskManager
from trading.runtime import Lifecycle, SystemState, SystemStateChanged
from trading.runtime.event_bus import EventBus
from trading.runtime.supervisor import Supervisor
from trading.strategy import Strategy
from trading.venues import Venue

ALL_ABCS = [
    Clock,
    Venue,
    MarketDataSource,
    FillEngine,
    Strategy,
    Executor,
    OrderManager,
    PositionManager,
    RiskManager,
    CircuitBreaker,
    EventBus,
    Supervisor,
    AlertRouter,
    AlertChannel,
    OrderJournal,
    AccountJournal,
    EventLog,
    AlertLog,
]


@pytest.mark.parametrize("cls", ALL_ABCS, ids=lambda c: c.__name__)
def test_abc_rejects_instantiation(cls: type) -> None:
    """Every ABC must refuse direct instantiation."""
    assert issubclass(cls, ABC), f"{cls.__name__} is not an ABC subclass"
    with pytest.raises(TypeError):
        cls()


def test_system_state_has_six_members() -> None:
    expected = {"BOOTING", "RECONCILING", "RUNNING", "QUARANTINE", "DRAINING", "STOPPED"}
    assert {s.name for s in SystemState} == expected


def test_alert_level_has_four_members() -> None:
    assert {level.name for level in AlertLevel} == {"INFO", "WARN", "ALERT", "CRITICAL"}


def test_trigger_action_has_four_members() -> None:
    assert {a.name for a in TriggerAction} == {
        "REJECT_NEW",
        "CANCEL_ALL",
        "QUARANTINE",
        "FLATTEN",
    }


def test_decision_adt_hierarchy() -> None:
    assert issubclass(Approved, Decision)
    assert issubclass(Rejected, Decision)
    assert isinstance(Approved(), Decision)
    assert isinstance(Rejected(reason="x"), Decision)


def test_decision_pattern_matching() -> None:
    def describe(d: Decision) -> str:
        match d:
            case Approved():
                return "ok"
            case Rejected(reason=r):
                return f"no: {r}"
        return "unreachable"

    assert describe(Approved()) == "ok"
    assert describe(Rejected(reason="lev > 3")) == "no: lev > 3"


def test_submit_result_adt_hierarchy() -> None:
    for cls in (Submitted, RiskRejected, VenueRejected, SubmissionFailed):
        assert issubclass(cls, SubmitResult)


def test_alert_is_frozen() -> None:
    now = datetime.now(timezone.utc)
    a = Alert(AlertLevel.INFO, "x", "msg", now)
    with pytest.raises(FrozenInstanceError):
        a.message = "mutated"  # type: ignore[misc]


def test_trigger_is_frozen() -> None:
    t = Trigger(breaker_name="X", action=TriggerAction.QUARANTINE, reason="r")
    with pytest.raises(FrozenInstanceError):
        t.reason = "mutated"  # type: ignore[misc]


def test_slots_dataclasses_have_no_dict() -> None:
    now = datetime.now(timezone.utc)
    a = Alert(AlertLevel.INFO, "x", "msg", now)
    assert not hasattr(a, "__dict__")


def test_lifecycle_structural_typing() -> None:
    """Any class with async start and stop satisfies Lifecycle."""

    class Conforming:
        async def start(self) -> None: ...

        async def stop(self) -> None: ...

    class Missing:
        async def start(self) -> None: ...

    assert isinstance(Conforming(), Lifecycle)
    assert not isinstance(Missing(), Lifecycle)


# ============================================================
# Behavioral contracts via in-memory fakes
# ============================================================


class _MemOrderJournal(OrderJournal):
    def __init__(self) -> None:
        self.orders: list[Order] = []
        self.fills: list[Fill] = []

    async def write_order(self, order: Order) -> None:
        self.orders.append(order)

    async def write_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    async def read_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        terminal = {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        }
        latest: dict[OrderId, Order] = {}
        for o in self.orders:
            latest[o.order_id] = o
        result = [o for o in latest.values() if o.status not in terminal]
        if symbol is not None:
            result = [o for o in result if o.symbol == symbol]
        return result

    async def read_order_history(self, order_id: OrderId) -> list[Order]:
        return [o for o in self.orders if o.order_id == order_id]

    async def read_fills_since(self, since: datetime) -> list[Fill]:
        return [f for f in self.fills if f.timestamp >= since]

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


async def test_order_journal_separates_open_from_terminal() -> None:
    j = _MemOrderJournal()
    now = datetime.now(timezone.utc)
    o1 = Order(
        order_id=OrderId("o1"),
        client_order_id=ClientOrderId("c1"),
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        price=Decimal("60000"),
        average_fill_price=None,
        status=OrderStatus.SUBMITTED,
        time_in_force=TimeInForce.GTC,
        reduce_only=False,
        created_at=now,
        updated_at=now,
    )
    o1_filled = replace(
        o1,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("1"),
        average_fill_price=Decimal("59999"),
        updated_at=now + timedelta(seconds=1),
    )
    o2_open = replace(
        o1,
        order_id=OrderId("o2"),
        client_order_id=ClientOrderId("c2"),
        status=OrderStatus.ACKED,
    )
    await j.write_order(o1)
    await j.write_order(o1_filled)
    await j.write_order(o2_open)

    open_orders = await j.read_open_orders()
    assert len(open_orders) == 1
    assert open_orders[0].order_id == OrderId("o2")

    history = await j.read_order_history(OrderId("o1"))
    assert len(history) == 2


_T = TypeVar("_T")


class _MemEventLog(EventLog):
    def __init__(self) -> None:
        self.events: list[object] = []

    async def write(self, event: object) -> None:
        self.events.append(event)

    async def read_since(self, event_type: type[_T], since: datetime) -> list[_T]:
        return [
            e
            for e in self.events
            if isinstance(e, event_type) and e.timestamp >= since  # type: ignore[attr-defined]
        ]

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


async def test_event_log_filters_by_type_and_time() -> None:
    log = _MemEventLog()
    now = datetime.now(timezone.utc)
    ev = SystemStateChanged(SystemState.BOOTING, SystemState.RUNNING, "startup", now)
    await log.write(ev)

    result = await log.read_since(SystemStateChanged, now - timedelta(seconds=10))
    assert len(result) == 1
    assert result[0] is ev


class _CapturingChannel(AlertChannel):
    def __init__(self, channel_name: str) -> None:
        self._name = channel_name
        self.sent: list[Alert] = []

    @property
    def name(self) -> str:
        return self._name

    async def send(self, alert: Alert) -> None:
        self.sent.append(alert)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _LevelRouter(AlertRouter):
    def __init__(self) -> None:
        self._by_channel: dict[AlertChannel, tuple[AlertLevel, ...]] = {}

    def register(self, channel: AlertChannel, *, levels: tuple[AlertLevel, ...]) -> None:
        self._by_channel[channel] = levels

    async def alert(self, alert: Alert) -> None:
        for ch, levels in self._by_channel.items():
            if alert.level in levels:
                with contextlib.suppress(Exception):
                    await ch.send(alert)

    async def start(self) -> None:
        for ch in self._by_channel:
            await ch.start()

    async def stop(self) -> None:
        for ch in self._by_channel:
            await ch.stop()


async def test_alert_router_dispatches_by_level() -> None:
    log_ch = _CapturingChannel("log")
    page_ch = _CapturingChannel("page")
    router = _LevelRouter()
    router.register(
        log_ch,
        levels=(AlertLevel.INFO, AlertLevel.WARN, AlertLevel.ALERT, AlertLevel.CRITICAL),
    )
    router.register(page_ch, levels=(AlertLevel.CRITICAL,))
    await router.start()
    now = datetime.now(timezone.utc)
    await router.alert(Alert(AlertLevel.INFO, "X", "hello", now))
    await router.alert(Alert(AlertLevel.CRITICAL, "X", "world", now))
    await router.stop()
    assert len(log_ch.sent) == 2
    assert len(page_ch.sent) == 1
    assert page_ch.sent[0].level == AlertLevel.CRITICAL
