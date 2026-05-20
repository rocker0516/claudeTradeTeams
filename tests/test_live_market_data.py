"""Tests for LiveMarketDataSource — Bybit public WS market data.

Two layers, both offline (no network, no pybit):
  1. _OrderbookState — pure book state machine. Pins the [CRITICAL]
     alignment finding that Bybit/pybit levels arrive UNSORTED, plus the
     snapshot-reset / delta-merge / size-0-delete semantics.
  2. LiveMarketDataSource — wired to a fake WS that lets tests fire raw
     messages; verifies domain conversion, that events reach async
     subscriber callbacks across the thread->loop bridge, and that a
     failing callback is routed to the AlertRouter (not silently dropped).
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from trading.alerts import AlertRouter
from trading.clock import RealClock
from trading.domain import (
    Alert,
    AlertLevel,
    FundingRate,
    OrderbookSnapshot,
    OrderSide,
    Symbol,
    Trade,
)
from trading.market_data.live import LiveMarketDataSource, _OrderbookState

BTC = Symbol("BTCUSDT")
_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ============================================================
# _OrderbookState (pure)
# ============================================================


def test_snapshot_sorts_unsorted_levels() -> None:
    """The [CRITICAL] finding: Bybit asks arrive unsorted; best_ask must
    still be the lowest ask after we sort."""
    state = _OrderbookState(BTC)
    state.apply(
        {
            # deliberately unsorted, mirroring real Bybit output
            "b": [["100.0", "1"], ["101.0", "2"], ["99.5", "3"]],
            "a": [["103.0", "1"], ["103.5", "2"], ["102.0", "5"]],
            "u": 10,
        },
        "snapshot",
    )
    snap = state.to_snapshot(_TS)
    assert [lvl.price for lvl in snap.bids] == [Decimal("101.0"), Decimal("100.0"), Decimal("99.5")]
    assert [lvl.price for lvl in snap.asks] == [Decimal("102.0"), Decimal("103.0"), Decimal("103.5")]
    assert snap.best_bid is not None and snap.best_bid.price == Decimal("101.0")
    assert snap.best_ask is not None and snap.best_ask.price == Decimal("102.0")
    assert snap.sequence == 10


def test_delta_updates_and_deletes_levels() -> None:
    state = _OrderbookState(BTC)
    state.apply({"b": [["100", "1"], ["99", "2"]], "a": [["101", "1"]], "u": 1}, "snapshot")
    # delta: change 100->5, delete 99 (size 0), add ask 102
    state.apply({"b": [["100", "5"], ["99", "0"]], "a": [["102", "3"]], "u": 2}, "delta")

    snap = state.to_snapshot(_TS)
    assert [(lvl.price, lvl.quantity) for lvl in snap.bids] == [(Decimal("100"), Decimal("5"))]
    assert [(lvl.price, lvl.quantity) for lvl in snap.asks] == [
        (Decimal("101"), Decimal("1")),
        (Decimal("102"), Decimal("3")),
    ]
    assert snap.sequence == 2


def test_snapshot_resets_whole_book() -> None:
    state = _OrderbookState(BTC)
    state.apply({"b": [["100", "1"], ["99", "1"]], "a": [["101", "1"]], "u": 1}, "snapshot")
    state.apply({"b": [["200", "2"]], "a": [["201", "2"]], "u": 9}, "snapshot")

    snap = state.to_snapshot(_TS)
    assert [lvl.price for lvl in snap.bids] == [Decimal("200")]
    assert [lvl.price for lvl in snap.asks] == [Decimal("201")]


# ============================================================
# Test doubles
# ============================================================


class _FakeWS:
    """Records stream subscriptions; tests fire messages via the handlers."""

    def __init__(self) -> None:
        self.ob_handlers: dict[str, Any] = {}
        self.trade_handlers: dict[str, Any] = {}
        self.ticker_handlers: dict[str, Any] = {}
        self.exited = False

    def orderbook_stream(self, depth: int, symbol: str, callback: Any) -> None:
        self.ob_handlers[symbol] = callback

    def trade_stream(self, symbol: str, callback: Any) -> None:
        self.trade_handlers[symbol] = callback

    def ticker_stream(self, symbol: str, callback: Any) -> None:
        self.ticker_handlers[symbol] = callback

    def exit(self) -> None:
        self.exited = True


class _RecordingRouter(AlertRouter):
    """Captures routed alerts; the rest of the ABC is a no-op."""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    async def alert(self, alert: Alert) -> None:
        self.alerts.append(alert)

    def register(self, channel: Any, *, levels: Any) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _make_source(fake: _FakeWS, router: AlertRouter | None = None) -> LiveMarketDataSource:
    return LiveMarketDataSource(
        ws_factory=lambda: fake,
        alert_router=router if router is not None else _RecordingRouter(),
        clock=RealClock(),
    )


# ============================================================
# LiveMarketDataSource (fake WS)
# ============================================================


async def test_orderbook_event_reaches_async_subscriber() -> None:
    received: list[OrderbookSnapshot] = []

    async def cb(snap: OrderbookSnapshot) -> None:
        received.append(snap)

    fake = _FakeWS()
    src = _make_source(fake)
    src.subscribe_orderbook(BTC, cb)
    await src.start()

    fake.ob_handlers["BTCUSDT"](
        {
            "type": "snapshot",
            "ts": 1779246006543,
            "data": {"s": "BTCUSDT", "b": [["100", "1"]], "a": [["101", "2"]], "u": 5},
        }
    )
    await asyncio.sleep(0.05)  # let run_coroutine_threadsafe-scheduled coro run

    assert len(received) == 1
    assert received[0].best_bid is not None and received[0].best_bid.price == Decimal("100")
    assert received[0].best_ask is not None and received[0].best_ask.price == Decimal("101")


async def test_trades_converted_and_dispatched() -> None:
    received: list[Trade] = []

    async def cb(trade: Trade) -> None:
        received.append(trade)

    fake = _FakeWS()
    src = _make_source(fake)
    src.subscribe_trades(BTC, cb)
    await src.start()

    fake.trade_handlers["BTCUSDT"](
        {
            "type": "snapshot",
            "data": [
                {"s": "BTCUSDT", "S": "Sell", "v": "0.014", "p": "76640.20", "T": 1779246006781},
                {"s": "BTCUSDT", "S": "Buy", "v": "0.001", "p": "76641.00", "T": 1779246006782},
            ],
        }
    )
    await asyncio.sleep(0.05)

    assert len(received) == 2
    assert received[0].side == OrderSide.SELL
    assert received[0].price == Decimal("76640.20")
    assert received[1].side == OrderSide.BUY


async def test_funding_fires_when_funding_rate_present() -> None:
    received: list[FundingRate] = []

    async def cb(fr: FundingRate) -> None:
        received.append(fr)

    fake = _FakeWS()
    src = _make_source(fake)
    src.subscribe_funding(BTC, cb)
    await src.start()

    fake.ticker_handlers["BTCUSDT"](
        {
            "type": "snapshot",
            "ts": 1779246005488,
            "data": {
                "symbol": "BTCUSDT",
                "fundingRate": "0.00009621",
                "nextFundingTime": "1779264000000",
            },
        }
    )
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].rate == Decimal("0.00009621")
    assert received[0].predicted_rate is None


async def test_funding_skipped_on_ticker_delta_without_funding_rate() -> None:
    received: list[FundingRate] = []

    async def cb(fr: FundingRate) -> None:
        received.append(fr)

    fake = _FakeWS()
    src = _make_source(fake)
    src.subscribe_funding(BTC, cb)
    await src.start()

    fake.ticker_handlers["BTCUSDT"](
        {"type": "delta", "ts": 1779246005588, "data": {"symbol": "BTCUSDT", "lastPrice": "76615"}}
    )
    await asyncio.sleep(0.05)

    assert received == []


async def test_subscriber_exception_is_routed_to_alert_router() -> None:
    """A failing async subscriber must page via AlertRouter, not vanish."""
    router = _RecordingRouter()
    fake = _FakeWS()
    src = _make_source(fake, router)

    async def bad_cb(_snap: OrderbookSnapshot) -> None:
        raise ValueError("boom")

    src.subscribe_orderbook(BTC, bad_cb)
    await src.start()

    fake.ob_handlers["BTCUSDT"](
        {
            "type": "snapshot",
            "ts": 1779246006543,
            "data": {"s": "BTCUSDT", "b": [["100", "1"]], "a": [["101", "2"]], "u": 5},
        }
    )
    await asyncio.sleep(0.05)

    assert len(router.alerts) == 1
    assert router.alerts[0].level == AlertLevel.ALERT
    assert router.alerts[0].component == "LiveMarketDataSource"
    assert "boom" in router.alerts[0].message
    assert router.alerts[0].correlation_key == "market_data:callback_error"


async def test_stop_exits_websocket() -> None:
    fake = _FakeWS()
    src = _make_source(fake)
    src.subscribe_orderbook(BTC, _noop_orderbook)
    await src.start()
    await src.stop()
    assert fake.exited is True


async def test_subscribe_after_start_raises() -> None:
    fake = _FakeWS()
    src = _make_source(fake)
    await src.start()
    with pytest.raises(RuntimeError, match="before start"):
        src.subscribe_orderbook(BTC, _noop_orderbook)


async def _noop_orderbook(_snap: OrderbookSnapshot) -> None:
    pass
