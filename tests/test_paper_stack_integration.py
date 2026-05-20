"""PaperVenue + Default stack end-to-end integration tests.

The first tests with PaperVenue in the chain. Prior integration tests
used `_DynamicVenue` (test helper) or NullVenue. This file verifies
that the production PaperVenue + DefaultOMS + DefaultPMS + Default
RiskManager + DrawdownBreaker + DefaultSupervisor all wire together
end-to-end.

Catches integration-level bugs that unit tests can't, e.g., ordering
of `on_fill` vs `on_order_update` callbacks (DefaultOMS expects fill
first so its local-fallback math doesn't double-count after the
authoritative overwrite).
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.alerts.channels import LogChannel
from trading.alerts.router import InMemoryAlertRouter
from trading.clock import RealClock
from trading.domain import (
    AlertLevel,
    FundingRate,
    Level,
    OrderbookSnapshot,
    OrderSide,
    OrderStatus,
    OrderType,
    Submitted,
    Symbol,
    TimeInForce,
    Trade,
    TradeIntent,
)
from trading.executor import DefaultExecutor
from trading.fill import ImmediateFillEngine
from trading.market_data import MarketDataSource
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
from trading.venues import PaperVenue


class _PushableMarketData(MarketDataSource):
    """MarketDataSource that lets tests fire orderbook events programmatically."""

    def __init__(self) -> None:
        self._orderbook_cbs: dict[Symbol, list[Callable[[OrderbookSnapshot], Awaitable[None]]]] = {}

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def subscribe_orderbook(
        self,
        symbol: Symbol,
        callback: Callable[[OrderbookSnapshot], Awaitable[None]],
    ) -> None:
        self._orderbook_cbs.setdefault(symbol, []).append(callback)

    def subscribe_trades(
        self, symbol: Symbol, callback: Callable[[Trade], Awaitable[None]]
    ) -> None:
        pass

    def subscribe_funding(
        self,
        symbol: Symbol,
        callback: Callable[[FundingRate], Awaitable[None]],
    ) -> None:
        pass

    async def push(self, snapshot: OrderbookSnapshot) -> None:
        for cb in self._orderbook_cbs.get(snapshot.symbol, []):
            await cb(snapshot)


@dataclass
class _Stack:
    supervisor: DefaultSupervisor
    bus: InMemoryEventBus
    venue: PaperVenue
    market_data: _PushableMarketData
    executor: DefaultExecutor
    oms: DefaultOrderManager
    pms: DefaultPositionManager
    order_journal: InMemoryOrderJournal
    account_journal: InMemoryAccountJournal
    clock: RealClock


def _build(
    *,
    initial_balance: str = "10000",
    drawdown_threshold: str = "0.05",
    tick_interval: float = 0.04,
) -> _Stack:
    clock = RealClock()
    bus = InMemoryEventBus()
    market_data = _PushableMarketData()

    venue = PaperVenue(
        market_data=market_data,
        fill_engine=ImmediateFillEngine(),
        clock=clock,
        symbols=[Symbol("BTCUSDT")],
        initial_balance=Decimal(initial_balance),
    )

    order_journal = InMemoryOrderJournal()
    account_journal = InMemoryAccountJournal()
    event_log = InMemoryEventLog()
    alert_log = InMemoryAlertLog()

    oms = DefaultOrderManager(venue=venue, journal=order_journal, clock=clock)
    pms = DefaultPositionManager(venue=venue, clock=clock, journal=account_journal)

    breakers = [
        DrawdownBreaker(
            bus=bus,
            threshold=Decimal(drawdown_threshold),
            window=timedelta(seconds=60),
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

    supervisor = DefaultSupervisor(
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

    return _Stack(
        supervisor=supervisor,
        bus=bus,
        venue=venue,
        market_data=market_data,
        executor=executor,
        oms=oms,
        pms=pms,
        order_journal=order_journal,
        account_journal=account_journal,
        clock=clock,
    )


def _book(*, bid: str, ask: str, seq: int = 1) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        symbol=Symbol("BTCUSDT"),
        bids=(Level(Decimal(bid), Decimal("100")),),
        asks=(Level(Decimal(ask), Decimal("100")),),
        sequence=seq,
        timestamp=datetime.now(timezone.utc),
    )


def _market_buy(quantity: str = "1") -> TradeIntent:
    return TradeIntent(
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=Decimal(quantity),
    )


def _market_sell(quantity: str = "1") -> TradeIntent:
    return TradeIntent(
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        quantity=Decimal(quantity),
    )


def _limit_buy(price: str, quantity: str = "1") -> TradeIntent:
    return TradeIntent(
        symbol=Symbol("BTCUSDT"),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        price=Decimal(price),
        time_in_force=TimeInForce.GTC,
    )


# ============================================================
# Boot / shutdown
# ============================================================


async def test_paper_stack_boots_and_shuts_down() -> None:
    stack = _build()
    await stack.supervisor.start()
    assert stack.supervisor.state == SystemState.RUNNING
    await stack.supervisor.stop()
    assert stack.supervisor.state == SystemState.STOPPED


# ============================================================
# Submit → fill chain (the integration test that catches the
# on_fill / on_order_update ordering bug)
# ============================================================


async def test_market_buy_via_executor_results_in_filled_order() -> None:
    """End-to-end: Executor.submit_intent → Risk → OMS → PaperVenue
    → fill → on_fill + on_order_update propagation → OMS cache + journal."""
    stack = _build()
    await stack.supervisor.start()
    # Prime book
    await stack.market_data.push(_book(bid="100", ask="101"))

    result = await stack.executor.submit_intent(_market_buy(quantity="2"))
    assert isinstance(result, Submitted)

    # The order in OMS should be FILLED with filled_quantity == request quantity.
    # If on_fill double-counts after on_order_update, this would be 4 instead of 2.
    order = await stack.oms.get_order(result.order_id)
    assert order is not None
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("2")

    await stack.supervisor.stop()


async def test_fill_writes_to_journal_exactly_once() -> None:
    stack = _build()
    await stack.supervisor.start()
    await stack.market_data.push(_book(bid="100", ask="101"))

    await stack.executor.submit_intent(_market_buy(quantity="1"))

    base = datetime.now(timezone.utc) - timedelta(seconds=10)
    fills = await stack.order_journal.read_fills_since(base)
    assert len(fills) == 1
    assert fills[0].quantity == Decimal("1")

    await stack.supervisor.stop()


# ============================================================
# PMS reconciles PaperVenue's position
# ============================================================


async def test_pms_sees_position_after_tick() -> None:
    stack = _build(tick_interval=0.04)
    await stack.supervisor.start()
    await stack.market_data.push(_book(bid="100", ask="101"))
    await stack.executor.submit_intent(_market_buy(quantity="2"))

    # Wait for ≥1 tick for PMS to reconcile from PaperVenue
    await asyncio.sleep(0.15)

    positions = await stack.pms.positions()
    assert len(positions) == 1
    assert positions[0].symbol == Symbol("BTCUSDT")
    assert positions[0].quantity == Decimal("2")
    assert positions[0].entry_price == Decimal("101")

    await stack.supervisor.stop()


async def test_pms_balance_reflects_unrealized_pnl() -> None:
    stack = _build(initial_balance="10000", tick_interval=0.04)
    await stack.supervisor.start()
    # Buy 10 @ 101
    await stack.market_data.push(_book(bid="100", ask="101"))
    await stack.executor.submit_intent(_market_buy(quantity="10"))
    await asyncio.sleep(0.1)

    # Move market up: mid = 105.5; unrealized = (105.5 - 101) * 10 = 45
    await stack.market_data.push(_book(bid="105", ask="106", seq=2))
    await asyncio.sleep(0.15)

    bal = await stack.pms.balance()
    assert bal.total == Decimal("10045")

    await stack.supervisor.stop()


# ============================================================
# Limit order: fill triggered by market data
# ============================================================


async def test_limit_order_fills_when_orderbook_crosses() -> None:
    stack = _build()
    await stack.supervisor.start()
    # Initial book: ask=110, limit buy at 105 won't cross
    await stack.market_data.push(_book(bid="109", ask="110"))
    result = await stack.executor.submit_intent(_limit_buy(price="105"))
    assert isinstance(result, Submitted)

    # Verify it's still open
    open_orders = await stack.oms.open_orders()
    assert len(open_orders) == 1

    # Move market: ask=104 < 105 → should fill
    await stack.market_data.push(_book(bid="103", ask="104", seq=2))

    # Fill happens during push (PaperVenue fires callbacks synchronously)
    order = await stack.oms.get_order(result.order_id)
    assert order is not None
    assert order.status == OrderStatus.FILLED

    await stack.supervisor.stop()


# ============================================================
# Cancel through Executor → PaperVenue
# ============================================================


async def test_cancel_via_executor_removes_open_order() -> None:
    stack = _build()
    await stack.supervisor.start()
    await stack.market_data.push(_book(bid="100", ask="101"))

    result = await stack.executor.submit_intent(_limit_buy(price="50"))
    assert isinstance(result, Submitted)
    assert len(await stack.oms.open_orders()) == 1

    ok = await stack.executor.cancel_order(result.order_id)
    assert ok is True
    assert len(await stack.venue.get_open_orders()) == 0

    await stack.supervisor.stop()


# ============================================================
# Drawdown trips via real PaperVenue PnL
# ============================================================


async def test_drawdown_breaker_trips_on_real_paper_pnl() -> None:
    """The headline test: real position-level PnL drives DrawdownBreaker.

    Flow:
      1. Buy 100 BTC @ 101 (notional 10100, but paper has no margin model)
      2. Move price down to 90 → unrealized loss = (90 - 101) * 100 = -1100
      3. balance.total drops from 10000 baseline → ~8900 (~11% drawdown)
      4. Tick → PMS reconciles → Risk.tick → DrawdownBreaker.evaluate trips
      5. BreakerTripped → Supervisor → QUARANTINE
    """
    stack = _build(
        initial_balance="10000",
        drawdown_threshold="0.05",
        tick_interval=0.04,
    )
    await stack.supervisor.start()
    # Let the first tick record balance peak ~ 10000
    await asyncio.sleep(0.1)

    # Buy 100 at 101
    await stack.market_data.push(_book(bid="100", ask="101"))
    await stack.executor.submit_intent(_market_buy(quantity="100"))
    await asyncio.sleep(0.1)

    # Market crashes to ~90
    await stack.market_data.push(_book(bid="89", ask="90", seq=2))
    # Wait for multiple ticks: PMS reconcile + Risk.tick + BreakerTripped dispatch
    await asyncio.sleep(0.3)

    assert stack.supervisor.state == SystemState.QUARANTINE

    await stack.supervisor.stop()


async def test_minor_pnl_swing_does_not_trip_drawdown() -> None:
    """Confirm the breaker DOESN'T fire on small PnL swings."""
    stack = _build(
        initial_balance="10000",
        drawdown_threshold="0.05",
        tick_interval=0.04,
    )
    await stack.supervisor.start()
    await asyncio.sleep(0.1)

    await stack.market_data.push(_book(bid="100", ask="101"))
    await stack.executor.submit_intent(_market_buy(quantity="10"))
    await asyncio.sleep(0.1)

    # Move down 2 ticks; unrealized = (99 - 101) * 10 = -20; balance = 9980
    # Drawdown ~ 0.2%, well under 5%
    await stack.market_data.push(_book(bid="98", ask="99", seq=2))
    await asyncio.sleep(0.2)

    assert stack.supervisor.state == SystemState.RUNNING

    await stack.supervisor.stop()


# ============================================================
# Acknowledge round-trip
# ============================================================


async def test_acknowledge_clears_latch_after_paper_drawdown() -> None:
    stack = _build(
        initial_balance="10000",
        drawdown_threshold="0.05",
        tick_interval=0.04,
    )
    await stack.supervisor.start()
    await asyncio.sleep(0.1)

    # Trigger drawdown
    await stack.market_data.push(_book(bid="100", ask="101"))
    await stack.executor.submit_intent(_market_buy(quantity="100"))
    await asyncio.sleep(0.1)
    await stack.market_data.push(_book(bid="89", ask="90", seq=2))
    await asyncio.sleep(0.3)
    assert stack.supervisor.state == SystemState.QUARANTINE

    # Close the bad position before ack (sell at market reduces exposure to ~flat
    # so further evaluations don't immediately re-trip from leftover loss)
    # Actually since DrawdownBreaker clears history on un-latch, even if PnL
    # remains poor the new baseline is "now"; no further drop = no re-trip.
    await stack.supervisor.acknowledge()
    assert stack.supervisor.state == SystemState.RUNNING

    await stack.supervisor.stop()


# ============================================================
# Sanity: realized PnL on close
# ============================================================


async def test_realized_pnl_appears_in_balance_after_close() -> None:
    stack = _build(initial_balance="10000", tick_interval=0.04)
    await stack.supervisor.start()

    # Buy 10 @ 101
    await stack.market_data.push(_book(bid="100", ask="101"))
    await stack.executor.submit_intent(_market_buy(quantity="10"))
    await asyncio.sleep(0.1)

    # Sell 10 @ 105 (bid=105 means sell crosses at 105)
    await stack.market_data.push(_book(bid="105", ask="106", seq=2))
    await stack.executor.submit_intent(_market_sell(quantity="10"))
    await asyncio.sleep(0.15)  # let PMS reconcile

    # Realized PnL = (105 - 101) * 10 = 40
    # No remaining position; balance = 10000 + 40 = 10040
    bal = await stack.pms.balance()
    assert bal.total == Decimal("10040")
    # No open positions
    assert await stack.pms.positions() == []

    await stack.supervisor.stop()
