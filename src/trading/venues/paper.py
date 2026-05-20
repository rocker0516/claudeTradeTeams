"""PaperVenue — live market data + simulated fills, no real exchange.

Subscribes to a MarketDataSource for orderbook updates. On each tick,
runs every open order on that symbol through the FillEngine; matching
orders fire on_order_update + on_fill callbacks (same path as a real
venue) and update internal position state.

Position math:
  - same-direction fill: weighted average entry price
  - opposite-direction fill: realize PnL on the closed quantity;
    remainder flips into a new position in the opposite direction
  - Balance = initial + realized PnL + unrealized PnL

Phase 0 simplifications:
  - No fees (ImmediateFillEngine.fee = 0; paper-venue doesn't add any)
  - No margin / leverage modeling — `available == total`, `leverage = 1`
  - No liquidation modeling — `liquidation_price = None`
  - No funding payments
  - No PnL realization on partial reductions of weighted-avg entry —
    PnL realized only when net qty changes direction or hits zero
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal

from ..clock import Clock
from ..domain import (
    Balance,
    Fill,
    Order,
    OrderbookSnapshot,
    OrderId,
    OrderRequest,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    Symbol,
    TimeInForce,
)
from ..fill import FillEngine
from ..market_data import MarketDataSource
from .base import Venue


@dataclass
class _PositionState:
    """Internal mutable state per symbol. Net qty + entry + realized."""

    net_qty: Decimal = Decimal("0")  # positive=long, negative=short
    entry_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


@dataclass
class _ApplyResult:
    new_state: _PositionState
    realized_pnl_delta: Decimal = field(default_factory=lambda: Decimal("0"))


def _apply_fill(state: _PositionState, fill: Fill) -> _ApplyResult:
    """Pure function: compute next position state given a fill.

    The fill's fee reduces realized PnL on every fill (open, add, or
    close). With a zero-fee engine (ImmediateFillEngine) this is a no-op;
    with OrderbookFillEngine the maker/taker fee flows into the balance.
    """
    signed = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
    new_qty = state.net_qty + signed
    fee = fill.fee

    # 1. Opening from flat
    if state.net_qty == 0:
        return _ApplyResult(
            _PositionState(new_qty, fill.price, state.realized_pnl - fee),
            realized_pnl_delta=-fee,
        )

    same_direction = (state.net_qty > 0 and signed > 0) or (state.net_qty < 0 and signed < 0)

    # 2. Adding to position in same direction → weighted avg entry
    if same_direction:
        old_notional = abs(state.net_qty) * state.entry_price
        added_notional = abs(signed) * fill.price
        new_avg = (old_notional + added_notional) / abs(new_qty)
        return _ApplyResult(
            _PositionState(new_qty, new_avg, state.realized_pnl - fee),
            realized_pnl_delta=-fee,
        )

    # 3. Opposite direction → reduce / close / flip
    closed = min(abs(state.net_qty), abs(signed))
    if state.net_qty > 0:
        # was long; closing portion is sold
        pnl = (fill.price - state.entry_price) * closed
    else:
        # was short; closing portion is bought back
        pnl = (state.entry_price - fill.price) * closed
    new_realized = state.realized_pnl + pnl - fee

    flipped = abs(signed) > abs(state.net_qty)
    new_entry = fill.price if flipped else state.entry_price
    return _ApplyResult(
        _PositionState(new_qty, new_entry, new_realized),
        realized_pnl_delta=pnl - fee,
    )


class PaperVenue(Venue):
    def __init__(
        self,
        *,
        market_data: MarketDataSource,
        fill_engine: FillEngine,
        clock: Clock,
        symbols: Iterable[Symbol],
        initial_balance: Decimal,
        currency: str = "USDT",
    ) -> None:
        self._market_data = market_data
        self._fill_engine = fill_engine
        self._clock = clock
        self._symbols = list(symbols)
        self._initial_balance = initial_balance
        self._currency = currency

        self._next_oid = 0
        self._open_orders: dict[OrderId, Order] = {}
        self._all_orders: dict[OrderId, Order] = {}  # for get_order lookup
        self._positions: dict[Symbol, _PositionState] = {}
        self._latest_book: dict[Symbol, OrderbookSnapshot] = {}

        self._order_cbs: list[Callable[[Order], Awaitable[None]]] = []
        self._fill_cbs: list[Callable[[Fill], Awaitable[None]]] = []

    async def start(self) -> None:
        for sym in self._symbols:
            self._market_data.subscribe_orderbook(sym, self._on_orderbook)

    async def stop(self) -> None:
        pass  # MarketDataSource isn't ours to stop

    # ===== Venue interface =====

    async def place_order(self, request: OrderRequest) -> OrderId:
        self._next_oid += 1
        oid = OrderId(f"paper-{self._next_oid}")
        now = self._clock.now()

        # POST_ONLY: reject at submission if the order would immediately
        # cross the spread (matching real-exchange behavior). If no book
        # snapshot is available yet we conservatively accept — same as a
        # real matching engine would handle an early order arrival.
        initial_status = OrderStatus.ACKED
        if request.time_in_force == TimeInForce.POST_ONLY:
            book = self._latest_book.get(request.symbol)
            if book is not None and self._would_cross_immediately(request, book):
                initial_status = OrderStatus.REJECTED

        order = Order(
            order_id=oid,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            type=request.type,
            quantity=request.quantity,
            filled_quantity=Decimal("0"),
            price=request.price,
            average_fill_price=None,
            status=initial_status,
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            created_at=now,
            updated_at=now,
        )
        self._all_orders[oid] = order
        if initial_status == OrderStatus.ACKED:
            self._open_orders[oid] = order

        # Emit ACK or REJECTED update so OMS sees the resulting state
        for cb in self._order_cbs:
            await cb(order)

        # Try immediate fill (only meaningful for ACKED orders)
        if initial_status == OrderStatus.ACKED:
            book = self._latest_book.get(request.symbol)
            if book is not None:
                await self._try_fill_order(oid, book)

        return oid

    @staticmethod
    def _would_cross_immediately(request: OrderRequest, book: OrderbookSnapshot) -> bool:
        """Returns True if a limit order at request.price would act as
        taker against the current book at submission time."""
        if request.price is None:
            return False  # market order — different code path
        if request.side == OrderSide.BUY:
            ask = book.best_ask
            return ask is not None and ask.price <= request.price
        bid = book.best_bid
        return bid is not None and bid.price >= request.price

    async def cancel_order(self, order_id: OrderId) -> bool:
        order = self._open_orders.pop(order_id, None)
        if order is None:
            return False
        cancelled = replace(order, status=OrderStatus.CANCELLED, updated_at=self._clock.now())
        self._all_orders[order_id] = cancelled
        for cb in self._order_cbs:
            await cb(cancelled)
        return True

    async def get_order(self, order_id: OrderId) -> Order | None:
        return self._all_orders.get(order_id)

    async def get_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        result = list(self._open_orders.values())
        if symbol is not None:
            result = [o for o in result if o.symbol == symbol]
        return result

    async def get_positions(self) -> list[Position]:
        positions: list[Position] = []
        for sym, state in self._positions.items():
            if state.net_qty == 0:
                continue
            positions.append(self._build_position(sym, state))
        return positions

    async def get_balance(self) -> Balance:
        total_realized = sum(
            (s.realized_pnl for s in self._positions.values()),
            start=Decimal("0"),
        )
        total_unrealized = Decimal("0")
        for sym, state in self._positions.items():
            if state.net_qty == 0:
                continue
            total_unrealized += self._unrealized_pnl(sym, state)

        total = self._initial_balance + total_realized + total_unrealized
        return Balance(
            currency=self._currency,
            total=total,
            available=total,  # no margin lockup in paper
            margin_used=Decimal("0"),
            updated_at=self._clock.now(),
        )

    def on_order_update(self, callback: Callable[[Order], Awaitable[None]]) -> None:
        self._order_cbs.append(callback)

    def on_fill(self, callback: Callable[[Fill], Awaitable[None]]) -> None:
        self._fill_cbs.append(callback)

    # ===== Internal =====

    async def _on_orderbook(self, snapshot: OrderbookSnapshot) -> None:
        self._latest_book[snapshot.symbol] = snapshot
        # Snapshot the open order IDs first because we'll mutate the dict
        ids_for_symbol = [
            oid for oid, order in self._open_orders.items() if order.symbol == snapshot.symbol
        ]
        for oid in ids_for_symbol:
            await self._try_fill_order(oid, snapshot)

    async def _try_fill_order(self, order_id: OrderId, snapshot: OrderbookSnapshot) -> None:
        order = self._open_orders.get(order_id)
        if order is None:
            return
        fill = self._fill_engine.try_fill(order, snapshot, self._clock.now())
        if fill is None:
            return
        await self._apply_fill_to_order(order, fill)

    async def _apply_fill_to_order(self, order: Order, fill: Fill) -> None:
        # Update order state
        new_filled = order.filled_quantity + fill.quantity
        if order.average_fill_price is None:
            new_avg = fill.price
        else:
            old_total = order.average_fill_price * order.filled_quantity
            new_avg = (old_total + fill.price * fill.quantity) / new_filled

        new_status = (
            OrderStatus.FILLED if new_filled >= order.quantity else OrderStatus.PARTIALLY_FILLED
        )
        updated = replace(
            order,
            filled_quantity=new_filled,
            average_fill_price=new_avg,
            status=new_status,
            updated_at=fill.timestamp,
        )

        if new_status == OrderStatus.FILLED:
            self._open_orders.pop(order.order_id, None)
        else:
            self._open_orders[order.order_id] = updated
        self._all_orders[order.order_id] = updated

        # Update position state
        state = self._positions.get(fill.symbol, _PositionState())
        result = _apply_fill(state, fill)
        self._positions[fill.symbol] = result.new_state

        # Fire fill BEFORE order update. DefaultOMS's `_on_fill` increments
        # filled_quantity from cached state as a fallback for venues that
        # don't reliably send order updates; `_on_order_update` then
        # overwrites with the authoritative state. If we fired order update
        # first, the cached filled would already reflect the new total, and
        # `_on_fill` would add the fill quantity on top → double count.
        for fill_cb in self._fill_cbs:
            await fill_cb(fill)
        for order_cb in self._order_cbs:
            await order_cb(updated)

    def _build_position(self, symbol: Symbol, state: _PositionState) -> Position:
        mark_price = self._mark_price(symbol, state.entry_price)
        side = PositionSide.LONG if state.net_qty > 0 else PositionSide.SHORT
        qty = abs(state.net_qty)
        if side == PositionSide.LONG:
            unrealized = (mark_price - state.entry_price) * qty
        else:
            unrealized = (state.entry_price - mark_price) * qty
        return Position(
            symbol=symbol,
            side=side,
            quantity=qty,
            entry_price=state.entry_price,
            mark_price=mark_price,
            unrealized_pnl=unrealized,
            realized_pnl=state.realized_pnl,
            leverage=Decimal("1"),
            liquidation_price=None,
            updated_at=self._clock.now(),
        )

    def _mark_price(self, symbol: Symbol, fallback: Decimal) -> Decimal:
        book = self._latest_book.get(symbol)
        if book is None:
            return fallback
        bid = book.best_bid
        ask = book.best_ask
        if bid is not None and ask is not None:
            return (bid.price + ask.price) / 2
        if bid is not None:
            return bid.price
        if ask is not None:
            return ask.price
        return fallback

    def _unrealized_pnl(self, symbol: Symbol, state: _PositionState) -> Decimal:
        mark = self._mark_price(symbol, state.entry_price)
        if state.net_qty > 0:
            return (mark - state.entry_price) * state.net_qty
        return (state.entry_price - mark) * abs(state.net_qty)
