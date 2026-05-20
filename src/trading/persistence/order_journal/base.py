"""Abstract OrderJournal — append-only persistence for order lifecycle + fills.

Stores one row per order state transition. The "current state" of an
order is the row with the latest timestamp; the order's history is
the full set of rows for that order_id ordered by timestamp.

On restart, OMS calls `read_open_orders()` to reconstruct working
orders, then reconciles against the venue.

Schema sketch (for concrete impls)::

  order_events:
    id (pk), order_id (indexed), client_order_id, symbol (indexed),
    side, type, quantity, filled_quantity, price, average_fill_price,
    status, time_in_force, reduce_only, created_at, updated_at (indexed)

  fills:
    id (pk), order_id (indexed), symbol, side, price, quantity, fee,
    fee_currency, is_maker, timestamp (indexed)

Both tables are append-only. No UPDATE, no DELETE.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from ...domain import Fill, Order, OrderId, Symbol


class OrderJournal(ABC):
    @abstractmethod
    async def write_order(self, order: Order) -> None:
        """Append the order's current full state as a new row.

        Called on every state transition (SUBMITTED -> ACKED -> ... ).
        The journal builds history by accumulation; never mutates rows.
        """
        ...

    @abstractmethod
    async def write_fill(self, fill: Fill) -> None:
        """Append a fill record. Fills are immutable once written."""
        ...

    @abstractmethod
    async def read_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        """Return the latest state of all orders whose status is not
        terminal (FILLED / CANCELLED / REJECTED / EXPIRED / FAILED).

        Used by OMS on restart to reconstruct working orders.
        """
        ...

    @abstractmethod
    async def read_order_history(self, order_id: OrderId) -> list[Order]:
        """Return every state transition for `order_id`, ordered by
        timestamp ascending."""
        ...

    @abstractmethod
    async def read_fills_since(self, since: datetime) -> list[Fill]:
        """Return all fills with timestamp >= `since`, ordered ascending."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
