"""Abstract OrderManager (OMS).

Owns the order state machine, maps TradeIntents to OrderRequests
(assigning a ClientOrderId for idempotency), and reconciles internal
state against the venue periodically.

OMS does NOT do risk checks — risk is enforced by Executor before the
intent reaches OMS. OMS trusts what it receives.
"""

from abc import ABC, abstractmethod

from ..domain import Order, OrderId, SubmitResult, Symbol, TradeIntent


class OrderManager(ABC):
    @abstractmethod
    async def submit(self, intent: TradeIntent) -> SubmitResult:
        """Translate intent into an OrderRequest (assigns ClientOrderId)
        and submit to the underlying Venue. Returns one of:
        Submitted / VenueRejected / SubmissionFailed."""
        ...

    @abstractmethod
    async def cancel(self, order_id: OrderId) -> bool: ...

    @abstractmethod
    async def cancel_all(self, symbol: Symbol | None = None) -> int:
        """Cancel all open orders, optionally filtered. Returns count cancelled."""
        ...

    @abstractmethod
    async def get_order(self, order_id: OrderId) -> Order | None: ...

    @abstractmethod
    async def open_orders(self, symbol: Symbol | None = None) -> list[Order]: ...
