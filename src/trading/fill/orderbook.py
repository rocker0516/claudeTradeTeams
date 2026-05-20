"""OrderbookFillEngine — book-aware fills with fees + slippage.

A more realistic FillEngine than ImmediateFillEngine, so paper trading is
an honest proxy for live rather than an optimistic one:

  - MARKET orders walk the book (consume successive levels), so the fill
    price is the size-weighted average across levels — i.e. slippage is
    modeled. Charged the taker fee. If the visible book is shallower than
    the order, the fill is PARTIAL (the caller retries next tick).
  - LIMIT orders, once their price is crossed, fill at THEIR OWN limit
    price (a resting maker order is filled at the price it posted, not at
    the opposite top-of-book like ImmediateFillEngine) and are charged the
    maker fee.

Contrast ImmediateFillEngine: zero fees, zero slippage, fills the whole
remaining at top-of-book — which makes paper look better than live.

is_maker is approximated by order type: MARKET=taker, LIMIT=maker. An
aggressive (marketable) limit is really a taker, but the venue's POST_ONLY
handling rejects would-cross post-only orders at submission, and the
dominant realism gains here are taker fees on market orders + book-walk
slippage. Fees default to Bybit USDT-perp (maker 0.02% / taker 0.055%).

Assumes book levels are sorted (asks ascending, bids descending), which
LiveMarketDataSource guarantees.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from ..domain import Fill, OrderSide, OrderType
from .base import FillEngine

if TYPE_CHECKING:
    from datetime import datetime

    from ..domain import Level, Order, OrderbookSnapshot

_DEFAULT_MAKER_FEE = Decimal("0.0002")
_DEFAULT_TAKER_FEE = Decimal("0.00055")


class OrderbookFillEngine(FillEngine):
    def __init__(
        self,
        *,
        maker_fee: Decimal = _DEFAULT_MAKER_FEE,
        taker_fee: Decimal = _DEFAULT_TAKER_FEE,
        fee_currency: str = "USDT",
    ) -> None:
        self._maker_fee = maker_fee
        self._taker_fee = taker_fee
        self._fee_currency = fee_currency

    def try_fill(self, order: Order, book: OrderbookSnapshot, timestamp: datetime) -> Fill | None:
        if order.symbol != book.symbol:
            return None
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return None
        if order.type == OrderType.MARKET:
            return self._market_fill(order, book, remaining, timestamp)
        if order.type == OrderType.LIMIT:
            return self._limit_fill(order, book, remaining, timestamp)
        return None  # stop / conditional orders unsupported

    def _market_fill(
        self, order: Order, book: OrderbookSnapshot, remaining: Decimal, timestamp: datetime
    ) -> Fill | None:
        levels = book.asks if order.side == OrderSide.BUY else book.bids
        filled, notional = _walk(levels, remaining)
        if filled == 0:
            return None
        avg_price = notional / filled
        return self._build(order, avg_price, filled, is_maker=False, timestamp=timestamp)

    def _limit_fill(
        self, order: Order, book: OrderbookSnapshot, remaining: Decimal, timestamp: datetime
    ) -> Fill | None:
        if order.price is None:
            return None
        if order.side == OrderSide.BUY:
            best = book.best_ask
            crossed = best is not None and best.price <= order.price
        else:
            best = book.best_bid
            crossed = best is not None and best.price >= order.price
        if not crossed:
            return None
        # A resting limit fills at its own posted price (maker), not at the
        # opposite top-of-book.
        return self._build(order, order.price, remaining, is_maker=True, timestamp=timestamp)

    def _build(
        self,
        order: Order,
        price: Decimal,
        quantity: Decimal,
        *,
        is_maker: bool,
        timestamp: datetime,
    ) -> Fill:
        fee_rate = self._maker_fee if is_maker else self._taker_fee
        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=price,
            quantity=quantity,
            fee=price * quantity * fee_rate,
            fee_currency=self._fee_currency,
            is_maker=is_maker,
            timestamp=timestamp,
        )


def _walk(levels: tuple[Level, ...], remaining: Decimal) -> tuple[Decimal, Decimal]:
    """Consume successive levels up to `remaining`. Returns (filled_qty,
    notional). filled_qty < remaining means the book was too shallow →
    a partial fill."""
    filled = Decimal("0")
    notional = Decimal("0")
    for level in levels:
        take = min(remaining - filled, level.quantity)
        filled += take
        notional += take * level.price
        if filled >= remaining:
            break
    return filled, notional
