"""Async wrapper around pybit's synchronous Unified Trading HTTP client.

Our Venue ABC is async; pybit's HTTP client is synchronous and blocking.
Each call is dispatched to a worker thread via asyncio.to_thread so the
event loop is never blocked. Phase 0 trades at most a couple of times per
funding interval, so thread-pool overhead is irrelevant.

Layering: this module knows pybit + Bybit parameter names, but NOT our
domain types. domain<->wire value conversion lives in adapters.py; request
construction + response parsing lives in venue.py. Keeping rest.py
domain-free means it can be unit-tested with a trivial fake client.

pybit raises (InvalidRequestError / FailedRequestError) on API / HTTP
errors, so callers normally get exceptions rather than retCode!=0 dicts.
We add a belt-and-braces retCode check in case a pybit version returns a
non-zero body without raising.

pybit is an OPTIONAL dependency ([bybit] extra), imported lazily in
create() so this package and its unit tests import fine without it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class BybitApiError(Exception):
    """Bybit returned retCode != 0 without pybit raising on its own."""

    def __init__(self, ret_code: int, ret_msg: str) -> None:
        super().__init__(f"Bybit API error {ret_code}: {ret_msg}")
        self.ret_code = ret_code
        self.ret_msg = ret_msg


class HTTPClient(Protocol):
    """The subset of pybit.unified_trading.HTTP that BybitRest calls.

    Declared as a Protocol so a fake client in tests is structurally
    accepted by mypy without importing pybit.
    """

    def get_server_time(self) -> dict[str, Any]: ...
    def get_wallet_balance(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_positions(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_open_orders(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_order_history(self, **kwargs: Any) -> dict[str, Any]: ...
    def place_order(self, **kwargs: Any) -> dict[str, Any]: ...
    def cancel_order(self, **kwargs: Any) -> dict[str, Any]: ...


class BybitRest:
    """Thin async + retCode-checking facade over a pybit HTTP client."""

    def __init__(self, http: HTTPClient, *, category: str = "linear") -> None:
        self._http = http
        self._category = category

    @classmethod
    def create(
        cls, *, api_key: str, api_secret: str, testnet: bool, category: str = "linear"
    ) -> BybitRest:
        from pybit.unified_trading import HTTP  # lazy: optional dependency

        http = HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)
        return cls(http, category=category)

    async def _call(self, fn: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        resp = await asyncio.to_thread(fn, **kwargs)
        ret_code = resp.get("retCode")
        if ret_code is not None and ret_code != 0:
            raise BybitApiError(int(ret_code), str(resp.get("retMsg", "")))
        return resp

    # ===== read =====

    async def get_server_time(self) -> dict[str, Any]:
        return await self._call(self._http.get_server_time)

    async def get_wallet_balance(self, *, account_type: str = "UNIFIED") -> dict[str, Any]:
        return await self._call(self._http.get_wallet_balance, accountType=account_type)

    async def get_positions(self, *, settle_coin: str = "USDT") -> dict[str, Any]:
        return await self._call(
            self._http.get_positions, category=self._category, settleCoin=settle_coin
        )

    async def get_open_orders(
        self, *, symbol: str | None = None, settle_coin: str = "USDT"
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"category": self._category}
        if symbol is not None:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = settle_coin
        return await self._call(self._http.get_open_orders, **params)

    async def get_order_realtime(self, *, order_id: str) -> dict[str, Any]:
        """Active order by id (open / recently-closed) via /order/realtime."""
        return await self._call(
            self._http.get_open_orders, category=self._category, orderId=order_id
        )

    async def get_order_history(self, *, order_id: str) -> dict[str, Any]:
        """Terminal order by id via /order/history (cancelled / filled)."""
        return await self._call(
            self._http.get_order_history, category=self._category, orderId=order_id
        )

    # ===== write =====

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        qty: str,
        order_link_id: str,
        price: str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "category": self._category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": qty,
            "orderLinkId": order_link_id,
            "reduceOnly": reduce_only,
        }
        if price is not None:
            params["price"] = price
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        return await self._call(self._http.place_order, **params)

    async def cancel_order(self, *, symbol: str, order_id: str) -> dict[str, Any]:
        return await self._call(
            self._http.cancel_order,
            category=self._category,
            symbol=symbol,
            orderId=order_id,
        )
