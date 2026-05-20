"""Unit tests for BybitRest — the async pybit-HTTP wrapper.

No network, no pybit: a fake synchronous HTTP client records calls and
returns canned responses. Verifies parameter injection (category /
settleCoin / accountType), conditional params (price / timeInForce only
when relevant), and the belt-and-braces retCode!=0 -> BybitApiError path.
"""

from typing import Any

import pytest

from trading.venues.bybit.rest import BybitApiError, BybitRest


class _FakeHTTP:
    """Records (method, kwargs) and returns canned or default-OK responses."""

    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._responses = responses or {}

    def _record(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((name, kwargs))
        return self._responses.get(name, {"retCode": 0, "retMsg": "OK", "result": {}})

    def get_server_time(self, **k: Any) -> dict[str, Any]:
        return self._record("get_server_time", **k)

    def get_wallet_balance(self, **k: Any) -> dict[str, Any]:
        return self._record("get_wallet_balance", **k)

    def get_positions(self, **k: Any) -> dict[str, Any]:
        return self._record("get_positions", **k)

    def get_open_orders(self, **k: Any) -> dict[str, Any]:
        return self._record("get_open_orders", **k)

    def get_order_history(self, **k: Any) -> dict[str, Any]:
        return self._record("get_order_history", **k)

    def place_order(self, **k: Any) -> dict[str, Any]:
        return self._record("place_order", **k)

    def cancel_order(self, **k: Any) -> dict[str, Any]:
        return self._record("cancel_order", **k)


async def test_get_wallet_balance_injects_account_type() -> None:
    http = _FakeHTTP()
    await BybitRest(http).get_wallet_balance()
    assert http.calls == [("get_wallet_balance", {"accountType": "UNIFIED"})]


async def test_get_positions_injects_category_and_settle_coin() -> None:
    http = _FakeHTTP()
    await BybitRest(http).get_positions()
    assert http.calls == [("get_positions", {"category": "linear", "settleCoin": "USDT"})]


async def test_get_open_orders_by_symbol_uses_symbol_not_settle() -> None:
    http = _FakeHTTP()
    await BybitRest(http).get_open_orders(symbol="BTCUSDT")
    assert http.calls == [("get_open_orders", {"category": "linear", "symbol": "BTCUSDT"})]


async def test_get_open_orders_without_symbol_uses_settle_coin() -> None:
    http = _FakeHTTP()
    await BybitRest(http).get_open_orders()
    assert http.calls == [("get_open_orders", {"category": "linear", "settleCoin": "USDT"})]


async def test_get_order_realtime_passes_order_id() -> None:
    http = _FakeHTTP()
    await BybitRest(http).get_order_realtime(order_id="oid-1")
    assert http.calls == [("get_open_orders", {"category": "linear", "orderId": "oid-1"})]


async def test_place_order_market_omits_price_and_tif() -> None:
    http = _FakeHTTP()
    await BybitRest(http).place_order(
        symbol="BTCUSDT", side="Buy", order_type="Market", qty="0.001", order_link_id="oms-x"
    )
    name, params = http.calls[0]
    assert name == "place_order"
    assert "price" not in params
    assert "timeInForce" not in params
    assert params["reduceOnly"] is False
    assert params["orderLinkId"] == "oms-x"
    assert params["category"] == "linear"


async def test_place_order_limit_includes_price_and_tif() -> None:
    http = _FakeHTTP()
    await BybitRest(http).place_order(
        symbol="BTCUSDT",
        side="Sell",
        order_type="Limit",
        qty="0.001",
        order_link_id="oms-y",
        price="80000",
        time_in_force="PostOnly",
        reduce_only=True,
    )
    _, params = http.calls[0]
    assert params["price"] == "80000"
    assert params["timeInForce"] == "PostOnly"
    assert params["reduceOnly"] is True
    assert params["side"] == "Sell"


async def test_cancel_order_passes_symbol_and_id() -> None:
    http = _FakeHTTP()
    await BybitRest(http).cancel_order(symbol="BTCUSDT", order_id="abc")
    assert http.calls == [
        ("cancel_order", {"category": "linear", "symbol": "BTCUSDT", "orderId": "abc"})
    ]


async def test_nonzero_retcode_raises_bybit_api_error() -> None:
    http = _FakeHTTP({"get_positions": {"retCode": 10001, "retMsg": "bad param", "result": {}}})
    with pytest.raises(BybitApiError) as exc:
        await BybitRest(http).get_positions()
    assert exc.value.ret_code == 10001
    assert "bad param" in exc.value.ret_msg


async def test_zero_retcode_returns_full_response() -> None:
    http = _FakeHTTP({"get_server_time": {"retCode": 0, "result": {"timeSecond": "1"}}})
    resp = await BybitRest(http).get_server_time()
    assert resp["result"]["timeSecond"] == "1"


async def test_custom_category_threads_through() -> None:
    http = _FakeHTTP()
    await BybitRest(http, category="inverse").get_positions()
    assert http.calls[0][1]["category"] == "inverse"
