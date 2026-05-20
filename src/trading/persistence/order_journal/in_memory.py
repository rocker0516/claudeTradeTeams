"""In-memory OrderJournal — list-backed."""

from datetime import datetime

from ...domain import Fill, Order, OrderId, OrderStatus, Symbol
from .base import OrderJournal

_TERMINAL_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
    OrderStatus.FAILED,
}


class InMemoryOrderJournal(OrderJournal):
    """List-backed OrderJournal for tests and POC.

    State is lost on process exit — use SQLitePersistence (TBD) for
    durability. All reads scan linearly; suitable for small N only.
    """

    def __init__(self) -> None:
        self._orders: list[Order] = []
        self._fills: list[Fill] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def write_order(self, order: Order) -> None:
        self._orders.append(order)

    async def write_fill(self, fill: Fill) -> None:
        self._fills.append(fill)

    async def read_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        latest: dict[OrderId, Order] = {}
        for o in self._orders:
            latest[o.order_id] = o
        result = [o for o in latest.values() if o.status not in _TERMINAL_STATUSES]
        if symbol is not None:
            result = [o for o in result if o.symbol == symbol]
        return result

    async def read_order_history(self, order_id: OrderId) -> list[Order]:
        return [o for o in self._orders if o.order_id == order_id]

    async def read_fills_since(self, since: datetime) -> list[Fill]:
        return [f for f in self._fills if f.timestamp >= since]
