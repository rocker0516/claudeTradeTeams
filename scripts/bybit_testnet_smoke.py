"""Bybit V5 testnet smoke test — verifies Venue ABC assumptions.

PURPOSE
    Exercises every operation we plan to implement in BybitVenue, against
    Bybit's testnet, BEFORE writing the actual BybitVenue. Prints raw
    responses so we can compare actual shapes against our domain types
    (Order / Position / Balance / Fill).

    Output goes to console (use ` > smoke_output.txt` to capture).
    Findings then feed back into docs/BYBIT_ABC_ALIGNMENT.md.

WHAT IT DOES NOT DO
    - Run as part of automated test suite (needs network + credentials)
    - Test MarketDataSource (public orderbook / trades / funding) — that's
      a separate smoke (TBD: scripts/bybit_market_data_smoke.py)
    - Test stop / conditional orders, sub-accounts, liquidation

PREREQUISITES
    1. Bybit testnet account: https://testnet.bybit.com
    2. API key + secret with read + trade permissions (testnet → API)
    3. pybit installed:  pip install -e ".[bybit]"
    4. Some testnet USDT (faucet on testnet.bybit.com)
    5. Env vars set:
         BYBIT_TESTNET_API_KEY
         BYBIT_TESTNET_API_SECRET

USAGE
    BYBIT_TESTNET_API_KEY=... BYBIT_TESTNET_API_SECRET=... \\
        python scripts/bybit_testnet_smoke.py

SAFETY
    Testnet only. Limit orders placed far below current price
    (deliberately won't fill, then cancelled). Quantity = 0.001 BTC.
"""

from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    from pybit.unified_trading import HTTP, WebSocket
except ImportError:
    sys.exit(
        "pybit not installed. Install with:  pip install -e '.[bybit]'\n"
        "Or directly:  pip install pybit>=5.10.0"
    )


TESTNET = True
SYMBOL = "BTCUSDT"
CATEGORY = "linear"  # USDT perpetuals
ORDER_QTY = "0.001"  # tiny


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


def show(label: str, obj: Any) -> None:
    """Pretty-print a Bybit response so it's easy to read."""
    print(f"\n--- {label} ---")
    if isinstance(obj, dict):
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def try_section(name: str, fn: Any) -> Any:
    """Run a section, catch + print errors, return result or None."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — exploratory script
        print(f"\n[ERROR in {name}]: {type(exc).__name__}: {exc}")
        return None


def _load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from a .env file into os.environ.

    Minimal hand-rolled loader (no python-dotenv dependency). Resolves
    .env at the repo root regardless of the current working directory.
    Existing environment variables win (setdefault) — so an explicitly
    exported var still overrides the file. Blank lines, comments (#...),
    and lines without '=' are skipped; surrounding quotes are stripped.
    """
    p = path or (Path(__file__).resolve().parent.parent / ".env")
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    _load_dotenv()
    api_key = os.environ.get("BYBIT_TESTNET_API_KEY")
    api_secret = os.environ.get("BYBIT_TESTNET_API_SECRET")
    if not api_key or not api_secret:
        sys.exit(
            "Missing credentials. Set:\n"
            "  BYBIT_TESTNET_API_KEY=...\n"
            "  BYBIT_TESTNET_API_SECRET=..."
        )

    http = HTTP(testnet=TESTNET, api_key=api_key, api_secret=api_secret)

    # ----------------------------------------------------------
    # 1. Server time — sanity check connection + clock skew
    # ----------------------------------------------------------
    section("1. Server time")
    resp = try_section("get_server_time", lambda: http.get_server_time())
    show("response", resp)
    print(
        "\nABC check: Our Clock.now() must align with venue server time.\n"
        "If skew > 1s, BybitVenue should normalize timestamps to server time."
    )

    # ----------------------------------------------------------
    # 2. Wallet balance — Unified Trading Account shape
    # ----------------------------------------------------------
    section("2. Wallet balance (Unified)")
    resp = try_section(
        "get_wallet_balance",
        lambda: http.get_wallet_balance(accountType="UNIFIED"),
    )
    show("response", resp)
    print(
        "\nABC check: Compare to domain.Balance:\n"
        "  currency / total / available / margin_used / updated_at\n"
        "Bybit returns totalEquity, totalAvailableBalance, totalMarginBalance,\n"
        "totalPerpUPL — note `total` likely maps to totalEquity (already includes\n"
        "unrealized PnL), and `margin_used` = totalEquity - totalAvailableBalance."
    )

    # ----------------------------------------------------------
    # 3. Positions — empty list expected unless you opened some
    # ----------------------------------------------------------
    section("3. Position list")
    resp = try_section(
        "get_positions",
        lambda: http.get_positions(category=CATEGORY, settleCoin="USDT"),
    )
    show("response", resp)
    print(
        "\nABC check: domain.Position fields:\n"
        "  symbol / side (LONG/SHORT/FLAT) / quantity / entry_price /\n"
        "  mark_price / unrealized_pnl / realized_pnl / leverage /\n"
        "  liquidation_price / updated_at\n"
        "Bybit: side='Buy'/'Sell'/'' (empty when flat), size, avgPrice,\n"
        "markPrice, unrealisedPnl, cumRealisedPnl, leverage, liqPrice.\n"
        "Note: cumRealisedPnl is cumulative since position opened, semantics\n"
        "match our realized_pnl reasonably well."
    )

    # ----------------------------------------------------------
    # 4. Existing open orders
    # ----------------------------------------------------------
    section("4. Existing open orders")
    resp = try_section(
        "get_open_orders",
        lambda: http.get_open_orders(category=CATEGORY, symbol=SYMBOL),
    )
    show("response", resp)

    # ----------------------------------------------------------
    # 5. Current ticker price (to place a non-crossing limit)
    # ----------------------------------------------------------
    section("5. Current ticker")
    resp = try_section(
        "get_tickers",
        lambda: http.get_tickers(category=CATEGORY, symbol=SYMBOL),
    )
    show("response", resp)
    last_price: Decimal | None = None
    try:
        last_price = Decimal(resp["result"]["list"][0]["lastPrice"])
        print(f"\nLast price: {last_price}")
    except Exception:  # noqa: BLE001
        print("Could not parse last price; skipping order placement.")
        return 0

    # ----------------------------------------------------------
    # 6. Place limit order — far below market, won't fill
    # ----------------------------------------------------------
    section("6. Place limit order (far OTM, will NOT fill)")
    # Bybit BTCUSDT tick size is 0.1; round to 1 decimal
    limit_price = (last_price * Decimal("0.5")).quantize(Decimal("0.1"))
    cid = f"smoke-{int(time.time())}"
    print(f"Placing BUY {ORDER_QTY} @ {limit_price} (mkt ~{last_price})")
    print(f"orderLinkId (our cid equivalent): {cid}")
    resp = try_section(
        "place_order",
        lambda: http.place_order(
            category=CATEGORY,
            symbol=SYMBOL,
            side="Buy",
            orderType="Limit",
            qty=ORDER_QTY,
            price=str(limit_price),
            timeInForce="GTC",
            orderLinkId=cid,
            reduceOnly=False,
        ),
    )
    show("response", resp)

    order_id: str | None = None
    try:
        order_id = resp["result"]["orderId"]
        print(f"\nBybit orderId: {order_id}")
    except Exception:  # noqa: BLE001
        print("Could not extract orderId — skipping subsequent steps.")
        return 0

    # Brief wait so WS / state propagates
    time.sleep(1.5)

    # ----------------------------------------------------------
    # 7. Get order by id — verify status shape
    # ----------------------------------------------------------
    section("7. Get order by id (should show 'New' status)")
    resp = try_section(
        "get_open_orders[byId]",
        lambda: http.get_open_orders(category=CATEGORY, orderId=order_id),
    )
    show("response", resp)
    print(
        "\nABC check: Bybit orderStatus values vs our domain.OrderStatus:\n"
        "  Bybit 'Created' (rare): SUBMITTED-ish, before matching engine\n"
        "  Bybit 'New':                         → ACKED\n"
        "  Bybit 'PartiallyFilled':             → PARTIALLY_FILLED\n"
        "  Bybit 'Filled':                      → FILLED\n"
        "  Bybit 'Cancelled':                   → CANCELLED\n"
        "  Bybit 'PartiallyFilledCanceled':     → CANCELLED (w/ leftover)\n"
        "  Bybit 'Rejected':                    → REJECTED\n"
        "  Bybit 'Untriggered/Triggered/...':   conditional orders, not Phase 0\n"
        "Our 'EXPIRED' has no direct Bybit equivalent (TIF.IOC/FOK auto-cancel,\n"
        "rest in 'Cancelled'). Our 'FAILED' is local-only (network errors)."
    )

    # ----------------------------------------------------------
    # 8. Cancel the order
    # ----------------------------------------------------------
    section("8. Cancel order")
    resp = try_section(
        "cancel_order",
        lambda: http.cancel_order(
            category=CATEGORY, symbol=SYMBOL, orderId=order_id
        ),
    )
    show("response", resp)

    # ----------------------------------------------------------
    # 9. Verify cancel via order history
    # ----------------------------------------------------------
    time.sleep(0.5)
    section("9. Order history (post-cancel)")
    resp = try_section(
        "get_order_history",
        lambda: http.get_order_history(category=CATEGORY, orderId=order_id),
    )
    show("response", resp)

    # ----------------------------------------------------------
    # 10. WebSocket private channel
    # ----------------------------------------------------------
    section("10. WebSocket private channel")
    print(
        "Subscribing to private order + execution streams.\n"
        "Placing then cancelling another order to observe events.\n"
        "Press Ctrl-C if it hangs > 30s."
    )
    try_section("ws_smoke", lambda: ws_smoke(api_key, api_secret, http, last_price))

    # ----------------------------------------------------------
    # Summary
    # ----------------------------------------------------------
    section("SUMMARY: action items")
    print(
        "Compare each section above to docs/BYBIT_ABC_ALIGNMENT.md.\n"
        "Specifically watch for:\n"
        "  - POST_ONLY: Bybit uses timeInForce='PostOnly' (TIF), not orderType.\n"
        "    Our domain has OrderType.POST_ONLY — MIGRATE to TimeInForce.\n"
        "  - Timestamps: Bybit returns millis as strings — BybitVenue must parse.\n"
        "  - All numeric fields are STRINGS in Bybit responses — convert to Decimal.\n"
        "  - Position side='' when flat — BybitVenue should map to PositionSide.FLAT\n"
        "    and `get_positions()` should filter these out (matches our PaperVenue).\n"
        "  - orderLinkId max 36 chars; our `oms-{uuid4().hex}` = 36 chars exactly. OK.\n"
    )

    return 0


def ws_smoke(
    api_key: str, api_secret: str, http: HTTP, last_price: Decimal
) -> None:
    """Open private WS, place + cancel an order, observe events."""
    received_orders: list[Any] = []
    received_executions: list[Any] = []

    def on_order(msg: Any) -> None:
        print(f"\n[WS ORDER]")
        show("msg", msg)
        received_orders.append(msg)

    def on_execution(msg: Any) -> None:
        print(f"\n[WS EXECUTION]")
        show("msg", msg)
        received_executions.append(msg)

    ws = WebSocket(
        testnet=TESTNET,
        channel_type="private",
        api_key=api_key,
        api_secret=api_secret,
    )
    ws.order_stream(callback=on_order)
    ws.execution_stream(callback=on_execution)

    # Give WS time to authenticate + subscribe
    time.sleep(2)

    # Place a marketable order (small market buy) so we get fill events
    cid = f"smoke-ws-{int(time.time())}"
    print(f"\nPlacing market BUY {ORDER_QTY} (will fill at market)")
    resp = http.place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side="Buy",
        orderType="Market",
        qty=ORDER_QTY,
        orderLinkId=cid,
        reduceOnly=False,
    )
    show("place response", resp)

    # Wait for WS events
    time.sleep(3)

    # Close the position (market sell same qty)
    print(f"\nPlacing market SELL {ORDER_QTY} (to close position)")
    resp = http.place_order(
        category=CATEGORY,
        symbol=SYMBOL,
        side="Sell",
        orderType="Market",
        qty=ORDER_QTY,
        orderLinkId=f"smoke-ws-close-{int(time.time())}",
        reduceOnly=True,
    )
    show("place response (close)", resp)

    time.sleep(3)

    print(f"\nTotal WS messages received:")
    print(f"  order events: {len(received_orders)}")
    print(f"  execution events: {len(received_executions)}")
    print(
        "\nABC check: domain.Fill fields:\n"
        "  order_id / symbol / side / price / quantity / fee / fee_currency /\n"
        "  is_maker / timestamp\n"
        "Bybit execution: orderId, symbol, side, execPrice, execQty, execFee,\n"
        "feeRate, isMaker, execTime, execType (Trade/Funding/AdlTrade/...).\n"
        "Note: Bybit has multiple execTypes — we only care about Trade for\n"
        "now; AdlTrade (auto-deleveraging) + Funding need handling later."
    )


if __name__ == "__main__":
    sys.exit(main())
