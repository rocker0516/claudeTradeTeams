"""Paper-trading demo — real Bybit market data + simulated fills, NO key.

Wires LiveMarketDataSource (real public WS) into the production paper
stack (PaperVenue + ImmediateFillEngine + Default OMS/PMS/Risk/Executor +
DefaultSupervisor) and runs a trivial demo strategy that alternates a tiny
market buy/sell every few seconds — so you can watch the whole pipeline
move: real orderbook -> strategy -> executor -> simulated fill ->
position / balance change.

NOT alpha, and NOT a faithful live proxy: ImmediateFillEngine assumes
top-of-book liquidity, zero fees, zero slippage, so paper fills here are
optimistic (see docs/BYBIT_MARKET_DATA_ALIGNMENT.md + the OOS note). It
exists to prove the plumbing works end-to-end against live data.

Public market data needs no credentials.

Usage:
    python scripts/paper_demo.py --seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from trading.alerts.channels import LogChannel
from trading.alerts.router import InMemoryAlertRouter
from trading.clock import Clock, RealClock
from trading.domain import (
    AlertLevel,
    Fill,
    FundingRate,
    Order,
    OrderbookSnapshot,
    OrderSide,
    OrderType,
    Symbol,
    Trade,
    TradeIntent,
)
from trading.executor import DefaultExecutor, Executor
from trading.fill import OrderbookFillEngine
from trading.market_data import LiveMarketDataSource, MarketDataSource
from trading.oms import DefaultOrderManager
from trading.persistence.account_journal import InMemoryAccountJournal
from trading.persistence.alert_log import InMemoryAlertLog
from trading.persistence.event_log import InMemoryEventLog
from trading.persistence.order_journal import InMemoryOrderJournal
from trading.pms import DefaultPositionManager
from trading.risk import DefaultRiskManager
from trading.runtime.event_bus import InMemoryEventBus
from trading.runtime.supervisor import DefaultSupervisor
from trading.strategy import Strategy
from trading.venues import PaperVenue

SYMBOL = Symbol("BTCUSDT")


class AlternatingDemoStrategy(Strategy):
    """Every `interval` seconds submit a tiny market order, alternating
    buy/sell. Subscribes itself to orderbook in __init__ (Supervisor does
    not wire strategies — see DefaultSupervisor docstring)."""

    def __init__(
        self,
        *,
        market_data: MarketDataSource,
        executor: Executor,
        clock: Clock,
        qty: Decimal,
        interval: float,
    ) -> None:
        self._executor = executor
        self._clock = clock
        self._qty = qty
        self._interval = interval
        self._last_submit: datetime | None = None
        self._side = OrderSide.BUY
        market_data.subscribe_orderbook(SYMBOL, self.on_orderbook)

    async def on_orderbook(self, snapshot: OrderbookSnapshot) -> None:
        now = self._clock.now()
        if self._last_submit is not None:
            elapsed = (now - self._last_submit).total_seconds()
            if elapsed < self._interval:
                return
        self._last_submit = now

        intent = TradeIntent(
            symbol=SYMBOL, side=self._side, type=OrderType.MARKET, quantity=self._qty
        )
        result = await self._executor.submit_intent(intent)
        bid = snapshot.best_bid.price if snapshot.best_bid else "?"
        ask = snapshot.best_ask.price if snapshot.best_ask else "?"
        print(
            f"[strategy] submit {self._side.value} {self._qty} "
            f"(book {bid}/{ask}) -> {type(result).__name__}"
        )
        self._side = OrderSide.SELL if self._side == OrderSide.BUY else OrderSide.BUY

    async def on_trade(self, trade: Trade) -> None:
        pass

    async def on_funding(self, rate: FundingRate) -> None:
        pass

    async def on_fill(self, fill: Fill) -> None:
        pass

    async def on_order_update(self, order: Order) -> None:
        pass


async def _run(seconds: int) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    clock = RealClock()
    bus = InMemoryEventBus()
    alert_router = InMemoryAlertRouter()
    alert_router.register(
        LogChannel(),
        levels=(AlertLevel.INFO, AlertLevel.WARN, AlertLevel.ALERT, AlertLevel.CRITICAL),
    )

    market_data = LiveMarketDataSource.create(alert_router=alert_router, clock=clock, testnet=False)
    venue = PaperVenue(
        market_data=market_data,
        fill_engine=OrderbookFillEngine(),
        clock=clock,
        symbols=[SYMBOL],
        initial_balance=Decimal("10000"),
    )

    order_journal = InMemoryOrderJournal()
    account_journal = InMemoryAccountJournal()
    oms = DefaultOrderManager(venue=venue, journal=order_journal, clock=clock)
    pms = DefaultPositionManager(venue=venue, clock=clock, journal=account_journal)
    risk = DefaultRiskManager(clock=clock, bus=bus, oms=oms, pms=pms, breakers=[])
    executor = DefaultExecutor(risk=risk, orders=oms, positions=pms)

    strategy = AlternatingDemoStrategy(
        market_data=market_data,
        executor=executor,
        clock=clock,
        qty=Decimal("0.01"),
        interval=5.0,
    )

    async def on_fill(fill: Fill) -> None:
        print(f"[fill]     {fill.side.value} {fill.quantity} @ {fill.price}")

    venue.on_fill(on_fill)

    supervisor = DefaultSupervisor(
        clock=clock,
        event_bus=bus,
        venue=venue,
        market_data=market_data,
        oms=oms,
        pms=pms,
        risk=risk,
        executor=executor,
        strategy=strategy,
        alert_router=alert_router,
        order_journal=order_journal,
        account_journal=account_journal,
        event_log=InMemoryEventLog(),
        alert_log=InMemoryAlertLog(),
        risk_tick_interval=0.5,
    )

    print(f"Starting paper demo for {seconds}s (real Bybit data, simulated fills, no key)...")
    await supervisor.start()
    try:
        for _ in range(seconds):
            await asyncio.sleep(1)
            balance = await pms.balance()
            positions = await pms.positions()
            pos = positions[0].quantity if positions else Decimal("0")
            print(f"[status]   balance={balance.total} position={pos}")
    finally:
        await supervisor.stop()
        print("Stopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper trading demo (real data, simulated fills)")
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(_run(args.seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
