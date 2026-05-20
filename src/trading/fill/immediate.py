"""ImmediateFillEngine — simplest fill model for paper / backtest.

Behavior:
  - MARKET orders fill the full remaining quantity at the top of the
    opposite side of the book.
  - LIMIT orders fill at best opposite (taker) IF the limit crosses
    the spread, otherwise stay open. POST_ONLY TIF doesn't change fill
    behavior here — once a POST_ONLY order has been accepted by the
    venue, it acts like a regular limit order. The rejection logic for
    would-cross POST_ONLY orders lives in the venue (PaperVenue), at
    submission time, not at fill time.
  - STOP_MARKET / STOP_LIMIT not supported — return None.

Limitations (intentional for Phase 0):
  - No partial fills based on book depth — assumes infinite liquidity
    at top of book.
  - No fees — Fill.fee = 0.
  - No slippage beyond crossing the spread.

For more realism, use OrderbookFillEngine (TBD) which walks the book.
"""

from datetime import datetime
from decimal import Decimal

from ..domain import Fill, Order, OrderbookSnapshot, OrderSide, OrderType
from .base import FillEngine


class ImmediateFillEngine(FillEngine):
    def __init__(self, *, fee_currency: str = "USDT") -> None:
        self._fee_currency = fee_currency

    def try_fill(
        self,
        order: Order,
        book: OrderbookSnapshot,
        timestamp: datetime,
    ) -> Fill | None:
        if order.symbol != book.symbol:
            return None

        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return None

        price = self._fill_price(order, book)
        if price is None:
            return None

        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=price,
            quantity=remaining,
            fee=Decimal("0"),
            fee_currency=self._fee_currency,
            is_maker=False,
            timestamp=timestamp,
        )

    def _fill_price(self, order: Order, book: OrderbookSnapshot) -> Decimal | None:
        if order.type == OrderType.MARKET:
            if order.side == OrderSide.BUY:
                return book.best_ask.price if book.best_ask else None
            return book.best_bid.price if book.best_bid else None

        if order.type == OrderType.LIMIT:
            if order.price is None:
                return None  # malformed limit order, shouldn't happen
            if order.side == OrderSide.BUY:
                ask = book.best_ask
                if ask is None or ask.price > order.price:
                    return None  # limit not crossed
                return ask.price  # cross the spread, fill at best ask
            bid = book.best_bid
            if bid is None or bid.price < order.price:
                return None
            return bid.price

        # STOP_MARKET, STOP_LIMIT — unsupported in Phase 0
        return None
