"""NullOrderManager — refuses every submission, holds no orders."""

from ..domain import Order, OrderId, SubmitResult, Symbol, TradeIntent, VenueRejected
from .base import OrderManager


class NullOrderManager(OrderManager):
    """OMS stub that returns VenueRejected for every submit.

    Use for idle-mode infrastructure tests. Strategies that try to
    submit intents through this OMS get an explicit terminal rejection.
    """

    async def submit(self, intent: TradeIntent) -> SubmitResult:
        return VenueRejected(reason="NullOrderManager does not route orders")

    async def cancel(self, order_id: OrderId) -> bool:
        return False

    async def cancel_all(self, symbol: Symbol | None = None) -> int:
        return 0

    async def get_order(self, order_id: OrderId) -> Order | None:
        return None

    async def open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        return []
