"""Bybit V5 PUBLIC WebSocket market-data smoke — NO API key needed.

Subscribes to the orderbook / public-trade / ticker streams on mainnet
linear and captures the first few raw messages of each, so we can align
Bybit's WS wire format against our OrderbookSnapshot / Trade / FundingRate
domain types BEFORE writing LiveMarketDataSource.

Things to confirm from the output:
  - orderbook: snapshot vs delta distinction (msg["type"]), bid/ask field
    names (b / a), the sequence field (u / seq), and how deletions are
    encoded (level with size "0"?).
  - trade: side encoding (Buy/Sell), price/size/timestamp field names.
  - ticker: fundingRate / nextFundingTime presence + whether ticker
    messages are snapshot+delta too (partial updates).

PUBLIC = no auth = no credentials. Safe to run anywhere.

Usage (PowerShell):
    python scripts/bybit_market_data_smoke.py |
        Out-File -Encoding utf8 md_smoke_output.txt
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

try:
    from pybit.unified_trading import WebSocket
except ImportError:
    sys.exit("pybit not installed.  pip install 'pybit>=5.10.0'")

SYMBOL = "BTCUSDT"
ORDERBOOK_DEPTH = 50
COLLECT_SECONDS = 12


def main() -> int:
    ob_msgs: list[Any] = []
    trade_msgs: list[Any] = []
    ticker_msgs: list[Any] = []

    def on_orderbook(msg: Any) -> None:
        if len(ob_msgs) < 5:
            ob_msgs.append(msg)

    def on_trade(msg: Any) -> None:
        if len(trade_msgs) < 3:
            trade_msgs.append(msg)

    def on_ticker(msg: Any) -> None:
        if len(ticker_msgs) < 3:
            ticker_msgs.append(msg)

    ws = WebSocket(testnet=False, channel_type="linear")
    ws.orderbook_stream(depth=ORDERBOOK_DEPTH, symbol=SYMBOL, callback=on_orderbook)
    ws.trade_stream(symbol=SYMBOL, callback=on_trade)
    ws.ticker_stream(symbol=SYMBOL, callback=on_ticker)

    print(f"Subscribed to {SYMBOL} orderbook.{ORDERBOOK_DEPTH} / publicTrade / tickers.")
    print(f"Collecting for ~{COLLECT_SECONDS}s (public, no auth)...\n")
    time.sleep(COLLECT_SECONDS)

    try:
        ws.exit()
    except Exception:  # noqa: BLE001 — best-effort teardown
        pass

    _dump("ORDERBOOK (msg[0] should be type=snapshot, rest delta)", ob_msgs)
    _dump("PUBLIC TRADES", trade_msgs)
    _dump("TICKER (funding lives here)", ticker_msgs)

    print(
        "\nABC alignment notes:\n"
        "  OrderbookSnapshot needs: symbol / bids / asks / sequence / timestamp.\n"
        "  Map Bybit data.b -> bids, data.a -> asks (each [price, size] strings),\n"
        "  data.u / data.seq -> sequence, msg.ts/cts -> timestamp. A delta with\n"
        "  size '0' deletes that price level. Snapshot resets the whole book.\n"
        "  Trade needs: symbol / price / quantity / side / timestamp.\n"
        "  FundingRate needs: symbol / rate / predicted_rate / next_funding_time."
    )
    return 0


def _dump(label: str, msgs: list[Any]) -> None:
    print(f"\n{'=' * 70}\n  {label}  (captured {len(msgs)})\n{'=' * 70}")
    for i, m in enumerate(msgs):
        print(f"\n--- #{i} ---")
        print(json.dumps(m, indent=2, default=str))


if __name__ == "__main__":
    sys.exit(main())
