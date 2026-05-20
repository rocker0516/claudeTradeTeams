"""DefaultOrderManager — wraps Venue with state cache + journal persistence.

Subscribes to Venue's on_order_update (authoritative state) and on_fill
(per-execution detail). Both paths write to OrderJournal so a restart
can re-hydrate from disk (restore logic TBD).

Translates TradeIntent into OrderRequest, generating a fresh
ClientOrderId each call. Note: this means retrying the same intent
produces a NEW cid — venue-level idempotency for retries is NOT
provided here. For Phase 0 POC this is acceptable; production needs
explicit cid passthrough on the strategy.submit_intent path.

cid_factory injection lets tests be deterministic; default uses uuid4.
"""

import uuid
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

from ..clock import Clock
from ..domain import (
    ClientOrderId,
    Fill,
    Order,
    OrderId,
    OrderRequest,
    OrderStatus,
    SubmissionFailed,
    SubmitResult,
    Submitted,
    Symbol,
    TradeIntent,
)
from ..persistence.order_journal import OrderJournal
from ..venues import Venue
from .base import OrderManager

_TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
    OrderStatus.FAILED,
}


def _default_cid_factory() -> ClientOrderId:
    return ClientOrderId(f"oms-{uuid.uuid4().hex}")


class DefaultOrderManager(OrderManager):
    def __init__(
        self,
        *,
        venue: Venue,
        journal: OrderJournal,
        clock: Clock,
        cid_factory: Callable[[], ClientOrderId] | None = None,
    ) -> None:
        self._venue = venue
        self._journal = journal
        self._clock = clock
        self._cid_factory = cid_factory or _default_cid_factory
        self._orders: dict[OrderId, Order] = {}
        venue.on_order_update(self._on_order_update)
        venue.on_fill(self._on_fill)

    # ===== Lifecycle (structural — matches Lifecycle Protocol) =====

    async def start(self) -> None:
        """Restore open-orders cache from the journal.

        Supervisor calls this after persistence is up but before the
        initial venue reconcile. Any non-terminal orders in the journal
        populate the cache so subsequent operations see last-known state
        — the cache is corrected by venue events as they arrive.
        """
        open_orders = await self._journal.read_open_orders()
        self._orders = {o.order_id: o for o in open_orders}

    async def stop(self) -> None:
        """No-op — cache is in-memory; the journal manages its own lifecycle."""

    async def submit(self, intent: TradeIntent) -> SubmitResult:
        cid = self._cid_factory()
        request = OrderRequest(
            symbol=intent.symbol,
            side=intent.side,
            type=intent.type,
            quantity=intent.quantity,
            client_order_id=cid,
            price=intent.price,
            time_in_force=intent.time_in_force,
            reduce_only=intent.reduce_only,
        )

        # Capture submission time BEFORE the venue call. If the venue (e.g.
        # PaperVenue) fires on_order_update / on_fill synchronously during
        # place_order, those events get fresher clock readings — keeping
        # the SUBMITTED audit entry earliest in journal time order.
        submitted_at = self._clock.now()

        try:
            order_id = await self._venue.place_order(request)
        except Exception as exc:
            # Transport-level failure — caller may retry. State unknown
            # until next reconciliation resolves it.
            return SubmissionFailed(reason=str(exc), is_retryable=True)

        order = Order(
            order_id=order_id,
            client_order_id=cid,
            symbol=intent.symbol,
            side=intent.side,
            type=intent.type,
            quantity=intent.quantity,
            filled_quantity=Decimal("0"),
            price=intent.price,
            average_fill_price=None,
            status=OrderStatus.SUBMITTED,
            time_in_force=intent.time_in_force,
            reduce_only=intent.reduce_only,
            created_at=submitted_at,
            updated_at=submitted_at,
        )
        # Always journal SUBMITTED — useful audit even when venue has
        # already pushed ACKED/FILLED via callbacks during place_order.
        await self._journal.write_order(order)
        # Only initialize cache if venue callbacks haven't populated it.
        # Real exchanges (WS callbacks, async) → cache is empty here.
        # Synchronous-callback venues (PaperVenue) → cache may already
        # hold ACKED/FILLED; setdefault doesn't overwrite that.
        self._orders.setdefault(order_id, order)
        return Submitted(order_id=order_id, client_order_id=cid)

    async def cancel(self, order_id: OrderId) -> bool:
        return await self._venue.cancel_order(order_id)

    async def cancel_all(self, symbol: Symbol | None = None) -> int:
        open_orders = await self.open_orders(symbol)
        count = 0
        for order in open_orders:
            if await self._venue.cancel_order(order.order_id):
                count += 1
        return count

    async def get_order(self, order_id: OrderId) -> Order | None:
        return self._orders.get(order_id)

    async def open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        result = [o for o in self._orders.values() if o.status not in _TERMINAL_STATUSES]
        if symbol is not None:
            result = [o for o in result if o.symbol == symbol]
        return result

    async def _on_order_update(self, order: Order) -> None:
        """Authoritative state from venue — overwrite cache + journal."""
        self._orders[order.order_id] = order
        await self._journal.write_order(order)

    async def _on_fill(self, fill: Fill) -> None:
        """Persist the fill; best-effort local order update.

        If on_order_update also fires (typical), it overwrites whatever
        we computed here — that's authoritative. Our local update is a
        fallback for venues that don't reliably send order updates with
        every fill.
        """
        await self._journal.write_fill(fill)

        order = self._orders.get(fill.order_id)
        if order is None:
            return  # Fill for unknown order — let reconciliation surface this

        new_filled = order.filled_quantity + fill.quantity
        if order.average_fill_price is None:
            new_avg = fill.price
        else:
            total_old = order.average_fill_price * order.filled_quantity
            new_avg = (total_old + fill.price * fill.quantity) / new_filled

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
        self._orders[fill.order_id] = updated
        await self._journal.write_order(updated)
