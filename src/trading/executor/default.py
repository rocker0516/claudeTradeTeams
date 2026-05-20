"""DefaultExecutor — composes RiskManager + OrderManager + PositionManager.

Routes submit_intent through risk check then OMS. Account queries
(positions / balance) delegate to PositionManager (PMS cache, not
the venue directly). Cancellation queries delegate to OMS.

This is the production Executor — there is no "Null" variant because
the composition handles null gracefully when wired with NullRisk +
NullOMS + NullPMS.
"""

from ..domain import (
    Approved,
    Balance,
    Order,
    OrderId,
    Position,
    Rejected,
    RiskRejected,
    SubmitResult,
    Symbol,
    TradeIntent,
)
from ..oms import OrderManager
from ..pms import PositionManager
from ..risk import RiskManager
from .base import Executor


class DefaultExecutor(Executor):
    def __init__(
        self,
        *,
        risk: RiskManager,
        orders: OrderManager,
        positions: PositionManager,
    ) -> None:
        self._risk = risk
        self._orders = orders
        self._positions = positions

    async def submit_intent(self, intent: TradeIntent) -> SubmitResult:
        decision = await self._risk.check(intent)
        match decision:
            case Approved():
                return await self._orders.submit(intent)
            case Rejected(reason=r, breaker_name=b):
                return RiskRejected(reason=r, breaker_name=b)
        raise TypeError(f"unexpected Decision variant: {type(decision).__name__}")

    async def cancel_order(self, order_id: OrderId) -> bool:
        return await self._orders.cancel(order_id)

    async def cancel_all(self, symbol: Symbol | None = None) -> int:
        return await self._orders.cancel_all(symbol)

    async def open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        return await self._orders.open_orders(symbol)

    async def positions(self) -> list[Position]:
        return await self._positions.positions()

    async def position(self, symbol: Symbol) -> Position | None:
        return await self._positions.position(symbol)

    async def balance(self) -> Balance:
        return await self._positions.balance()
