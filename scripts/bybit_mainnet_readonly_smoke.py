"""Bybit V5 MAINNET read-only smoke — verifies BybitVenue read-path shapes.

READ-ONLY BY CONSTRUCTION: this script contains NO place_order /
cancel_order / WebSocket-order calls. It cannot trade, regardless of the
API key's permissions. Safe to run against mainnet with a real key.

Verifies the response shapes BybitVenue Slice 1 (read path) depends on:
    server-time / wallet-balance / positions / open-orders /
    order-history / ticker

Findings feed back into docs/BYBIT_ABC_ALIGNMENT.md §5 (read-path items).

Credentials (.env):
    BYBIT_MAINNET_API_KEY / BYBIT_MAINNET_API_SECRET
Falls back to BYBIT_TESTNET_* with a warning — a key that returns 10003
on testnet is almost certainly a mainnet key filed under the wrong name.

Usage (PowerShell):
    python scripts/bybit_mainnet_readonly_smoke.py |
        Out-File -Encoding utf8 mainnet_smoke_output.txt
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from pybit.unified_trading import HTTP
except ImportError:
    sys.exit("pybit not installed.  pip install 'pybit>=5.10.0'")

# Reuse the helpers from the testnet smoke (importing it only runs its
# module-level constants + pybit import, not main()).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bybit_testnet_smoke import _load_dotenv, section, show, try_section  # noqa: E402

CATEGORY = "linear"  # USDT perpetuals
SYMBOL = "BTCUSDT"


def main() -> int:
    _load_dotenv()
    api_key = os.environ.get("BYBIT_MAINNET_API_KEY") or os.environ.get("BYBIT_TESTNET_API_KEY")
    api_secret = os.environ.get("BYBIT_MAINNET_API_SECRET") or os.environ.get(
        "BYBIT_TESTNET_API_SECRET"
    )
    if not api_key or not api_secret:
        sys.exit("Missing credentials: set BYBIT_MAINNET_API_KEY / _SECRET in .env")
    if not os.environ.get("BYBIT_MAINNET_API_KEY"):
        print(
            "WARNING: no BYBIT_MAINNET_API_KEY set — using the key under "
            "BYBIT_TESTNET_* against MAINNET.\n"
            "         This is read-only (no order calls exist here), so it is safe.\n"
        )

    http = HTTP(testnet=False, api_key=api_key, api_secret=api_secret)

    # 1. Server time — connectivity + clock skew baseline
    section("1. Server time")
    show("response", try_section("get_server_time", lambda: http.get_server_time()))

    # 2. Wallet balance — Unified Trading Account shape (domain.Balance)
    section("2. Wallet balance (Unified)")
    show(
        "response",
        try_section(
            "get_wallet_balance",
            lambda: http.get_wallet_balance(accountType="UNIFIED"),
        ),
    )

    # 3. Positions — domain.Position shape, side='' when flat
    section("3. Positions")
    show(
        "response",
        try_section(
            "get_positions",
            lambda: http.get_positions(category=CATEGORY, settleCoin="USDT"),
        ),
    )

    # 4. Open orders — domain.Order shape (active)
    section("4. Open orders")
    show(
        "response",
        try_section(
            "get_open_orders",
            lambda: http.get_open_orders(category=CATEGORY, settleCoin="USDT"),
        ),
    )

    # 5. Order history — domain.Order shape (closed/terminal), status spellings
    section("5. Order history (last 5)")
    show(
        "response",
        try_section(
            "get_order_history",
            lambda: http.get_order_history(category=CATEGORY, limit=5),
        ),
    )

    # 6. Ticker — public, mark/last price for get_positions mark fallback
    section("6. Ticker (public)")
    show(
        "response",
        try_section(
            "get_tickers",
            lambda: http.get_tickers(category=CATEGORY, symbol=SYMBOL),
        ),
    )

    section("DONE — read-only, no orders placed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
