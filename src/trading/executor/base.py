"""Abstract Executor — the strategy-facing facade.

Strategies talk to the Executor and ONLY the Executor for trade
actions and account queries. The default concrete implementation
composes RiskManager + OrderManager + PositionManager behind this
interface; strategies do not need to know that layering exists.

Design contract:
  - submit_intent always goes through risk check first
  - There is no public path from Strategy to Venue
  - Account queries (positions / balance) read from PMS cache,
    not directly from Venue
"""

from abc import ABC, abstractmethod

from ..domain import (
    Balance,
    Order,
    OrderId,
    Position,
    SubmitResult,
    Symbol,
    TradeIntent,
)


class Executor(ABC):
    @abstractmethod
    async def submit_intent(self, intent: TradeIntent) -> SubmitResult:
        """Risk-checked submission.

        The intent is evaluated by RiskManager.check(); if approved it
        is forwarded to OrderManager.submit(). The returned SubmitResult
        reflects the outcome of whichever stage produced a terminal
        result (RiskRejected from risk, or Submitted / VenueRejected /
        SubmissionFailed from OMS).
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: OrderId) -> bool: ...

    @abstractmethod
    async def cancel_all(self, symbol: Symbol | None = None) -> int: ...

    @abstractmethod
    async def open_orders(self, symbol: Symbol | None = None) -> list[Order]: ...

    @abstractmethod
    async def positions(self) -> list[Position]: ...

    @abstractmethod
    async def position(self, symbol: Symbol) -> Position | None: ...

    @abstractmethod
    async def balance(self) -> Balance: ...
