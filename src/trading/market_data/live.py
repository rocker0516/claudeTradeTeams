"""LiveMarketDataSource — Bybit V5 public WebSocket market data.

Wraps pybit's public WebSocket (linear) as a MarketDataSource. No API key
needed — public streams are unauthenticated.

Orderbook handling (see docs/BYBIT_MARKET_DATA_ALIGNMENT.md):
  - pybit may deliver a "snapshot" (full book) or "delta" (incremental)
    message. _OrderbookState handles both: snapshot resets the book, delta
    merges it (a level with size "0" is a deletion).
  - [CRITICAL] Bybit/pybit do NOT guarantee level ordering, so we sort on
    every emit (bids descending, asks ascending) before building the
    OrderbookSnapshot — otherwise best_bid/best_ask (index [0]) are wrong.

Threading: pybit invokes callbacks on its own receiver thread. start()
captures the running event loop; thread callbacks hand domain events back
to the loop via asyncio.run_coroutine_threadsafe, because subscriber
callbacks are async. Subscribe BEFORE start() — start() wires one WS stream
per already-subscribed symbol.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from ..bybit import wire
from ..domain import Alert, AlertLevel, FundingRate, Level, OrderbookSnapshot, Symbol, Trade
from .base import FundingCallback, MarketDataSource, OrderbookCallback, TradeCallback

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ..alerts import AlertRouter
    from ..clock import Clock

logger = logging.getLogger(__name__)


class _OrderbookState:
    """Mutable per-symbol book. Single-threaded (pybit receiver thread)."""

    def __init__(self, symbol: Symbol) -> None:
        self._symbol = symbol
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._last_update_id: int | None = None

    def apply(self, data: dict[str, Any], msg_type: str) -> None:
        if msg_type == "snapshot":
            self._bids.clear()
            self._asks.clear()
        else:
            new_id = data.get("u")
            if (
                self._last_update_id is not None
                and isinstance(new_id, int)
                and new_id <= self._last_update_id
            ):
                logger.warning(
                    "orderbook %s: non-increasing update id %s <= %s (possible gap)",
                    self._symbol,
                    new_id,
                    self._last_update_id,
                )
        self._apply_side(self._bids, data.get("b", []))
        self._apply_side(self._asks, data.get("a", []))
        update_id = data.get("u")
        if isinstance(update_id, int):
            self._last_update_id = update_id

    @staticmethod
    def _apply_side(side: dict[Decimal, Decimal], levels: list[list[str]]) -> None:
        for level in levels:
            price = Decimal(level[0])
            size = Decimal(level[1])
            if size == 0:
                side.pop(price, None)  # deletion
            else:
                side[price] = size

    def to_snapshot(self, timestamp: datetime) -> OrderbookSnapshot:
        # Sort on every emit — Bybit/pybit do not guarantee ordering.
        bids = tuple(Level(p, self._bids[p]) for p in sorted(self._bids, reverse=True))
        asks = tuple(Level(p, self._asks[p]) for p in sorted(self._asks))
        return OrderbookSnapshot(
            symbol=self._symbol,
            bids=bids,
            asks=asks,
            sequence=self._last_update_id or 0,
            timestamp=timestamp,
        )


class WebSocketClient(Protocol):
    """Subset of pybit.unified_trading.WebSocket that LiveMarketDataSource uses.

    A Protocol so a fake WS in tests is structurally accepted by mypy
    without importing pybit.
    """

    def orderbook_stream(self, depth: int, symbol: str, callback: Callable[[Any], None]) -> None: ...
    def trade_stream(self, symbol: str, callback: Callable[[Any], None]) -> None: ...
    def ticker_stream(self, symbol: str, callback: Callable[[Any], None]) -> None: ...
    def exit(self) -> None: ...


class LiveMarketDataSource(MarketDataSource):
    def __init__(
        self,
        *,
        ws_factory: Callable[[], WebSocketClient],
        alert_router: AlertRouter,
        clock: Clock,
        depth: int = 50,
    ) -> None:
        self._ws_factory = ws_factory
        self._alert_router = alert_router
        self._clock = clock
        self._depth = depth
        self._ws: WebSocketClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ob_subs: dict[Symbol, list[OrderbookCallback]] = defaultdict(list)
        self._trade_subs: dict[Symbol, list[TradeCallback]] = defaultdict(list)
        self._funding_subs: dict[Symbol, list[FundingCallback]] = defaultdict(list)
        self._books: dict[Symbol, _OrderbookState] = {}
        self._started = False

    @classmethod
    def create(
        cls, *, alert_router: AlertRouter, clock: Clock, testnet: bool, depth: int = 50
    ) -> LiveMarketDataSource:
        def factory() -> WebSocketClient:
            from pybit.unified_trading import WebSocket  # lazy: optional dependency

            ws: WebSocketClient = WebSocket(testnet=testnet, channel_type="linear")
            return ws

        return cls(ws_factory=factory, alert_router=alert_router, clock=clock, depth=depth)

    # ===== MarketDataSource interface =====

    def subscribe_orderbook(self, symbol: Symbol, callback: OrderbookCallback) -> None:
        self._require_not_started()
        self._ob_subs[symbol].append(callback)

    def subscribe_trades(self, symbol: Symbol, callback: TradeCallback) -> None:
        self._require_not_started()
        self._trade_subs[symbol].append(callback)

    def subscribe_funding(self, symbol: Symbol, callback: FundingCallback) -> None:
        self._require_not_started()
        self._funding_subs[symbol].append(callback)

    def _require_not_started(self) -> None:
        if self._started:
            raise RuntimeError(
                "subscribe_*() must be called before start() — LiveMarketDataSource "
                "wires one WS stream per subscribed symbol at start()"
            )

    async def start(self) -> None:
        self._started = True
        self._loop = asyncio.get_running_loop()
        ws = self._ws_factory()
        self._ws = ws
        for symbol in self._ob_subs:
            self._books[symbol] = _OrderbookState(symbol)
            ws.orderbook_stream(
                depth=self._depth, symbol=symbol, callback=self._orderbook_handler(symbol)
            )
        for symbol in self._trade_subs:
            ws.trade_stream(symbol=symbol, callback=self._trade_handler(symbol))
        for symbol in self._funding_subs:
            ws.ticker_stream(symbol=symbol, callback=self._funding_handler(symbol))

    async def stop(self) -> None:
        if self._ws is not None:
            self._ws.exit()
            self._ws = None

    # ===== WS handlers (run on pybit's receiver thread) =====

    def _orderbook_handler(self, symbol: Symbol) -> Callable[[Any], None]:
        def handler(msg: Any) -> None:
            book = self._books[symbol]
            book.apply(msg["data"], msg.get("type", "delta"))
            snapshot = book.to_snapshot(wire.to_datetime(msg["ts"]))
            self._dispatch(self._ob_subs[symbol], snapshot)

        return handler

    def _trade_handler(self, symbol: Symbol) -> Callable[[Any], None]:
        def handler(msg: Any) -> None:
            for entry in msg["data"]:
                trade = Trade(
                    symbol=Symbol(entry["s"]),
                    price=wire.to_decimal(entry["p"]),
                    quantity=wire.to_decimal(entry["v"]),
                    side=wire.side_from_bybit(entry["S"]),
                    timestamp=wire.to_datetime(entry["T"]),
                )
                self._dispatch(self._trade_subs[symbol], trade)

        return handler

    def _funding_handler(self, symbol: Symbol) -> Callable[[Any], None]:
        def handler(msg: Any) -> None:
            data = msg["data"]
            rate = data.get("fundingRate")
            if not rate:  # ticker delta without funding fields
                return
            funding = FundingRate(
                symbol=Symbol(data["symbol"]),
                rate=wire.to_decimal(rate),
                predicted_rate=None,
                next_funding_time=wire.to_datetime(data["nextFundingTime"]),
                timestamp=wire.to_datetime(msg["ts"]),
            )
            self._dispatch(self._funding_subs[symbol], funding)

        return handler

    def _dispatch(self, callbacks: list[Any], event: Any) -> None:
        """Hand a domain event from the WS thread back to the event loop."""
        loop = self._loop
        if loop is None:
            return
        for cb in callbacks:
            asyncio.run_coroutine_threadsafe(self._safe_call(cb, event), loop)

    async def _safe_call(self, callback: Any, event: Any) -> None:
        """Run a subscriber callback, routing any exception to the AlertRouter.

        A failing subscriber must surface ("page on anomaly, not silent
        retry") rather than vanish inside a dropped run_coroutine_threadsafe
        future.
        """
        try:
            await callback(event)
        except Exception as exc:
            await self._alert_router.alert(
                Alert(
                    level=AlertLevel.ALERT,
                    component="LiveMarketDataSource",
                    message=f"market-data subscriber callback raised: {exc!r}",
                    timestamp=self._clock.now(),
                    correlation_key="market_data:callback_error",
                )
            )
