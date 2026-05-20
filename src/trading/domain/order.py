"""Order-related domain types.

Prices and quantities use Decimal (never float) to avoid binary
representation errors that compound across many fills.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .ids import ClientOrderId, OrderId, Symbol


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    """Order lifetime + execution policy.

    POST_ONLY is here (not OrderType) because real exchanges (Bybit V5,
    Binance, OKX, Hyperliquid) treat post-only as a TIF flag on a limit
    order — not as a separate order type. A POST_ONLY order is rejected
    at submission time if it would immediately cross the spread; once
    accepted, it behaves like a regular limit order.
    """

    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    POST_ONLY = "POST_ONLY"


class OrderStatus(str, Enum):
    SUBMITTED = "submitted"
    ACKED = "acked"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Outbound order request submitted to a Venue."""

    symbol: Symbol
    side: OrderSide
    type: OrderType
    quantity: Decimal
    client_order_id: ClientOrderId
    price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    stop_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Order:
    """Lifecycle view of an order — updated by OMS based on Venue events."""

    order_id: OrderId
    client_order_id: ClientOrderId
    symbol: Symbol
    side: OrderSide
    type: OrderType
    quantity: Decimal
    filled_quantity: Decimal
    price: Decimal | None
    average_fill_price: Decimal | None
    status: OrderStatus
    time_in_force: TimeInForce
    reduce_only: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """Strategy's intent to trade — pre-risk-check, pre-OMS.

    The risk layer validates, then OMS translates this into an OrderRequest
    by assigning a ClientOrderId.
    """

    symbol: Symbol
    side: OrderSide
    type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    reason: str = ""
