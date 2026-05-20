"""In-memory AccountJournal — list-backed snapshots."""

from datetime import datetime

from ...domain import Balance, Position, Symbol
from .base import AccountJournal


class InMemoryAccountJournal(AccountJournal):
    """List-backed AccountJournal for tests and POC."""

    def __init__(self) -> None:
        self._positions: list[Position] = []
        self._balances: list[Balance] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def write_position_snapshot(self, position: Position) -> None:
        self._positions.append(position)

    async def write_balance_snapshot(self, balance: Balance) -> None:
        self._balances.append(balance)

    async def read_latest_position(self, symbol: Symbol) -> Position | None:
        for p in reversed(self._positions):
            if p.symbol == symbol:
                return p
        return None

    async def read_latest_balance(self) -> Balance | None:
        return self._balances[-1] if self._balances else None

    async def read_all_latest_positions(self) -> list[Position]:
        latest_per_symbol: dict[Symbol, Position] = {}
        for p in self._positions:
            existing = latest_per_symbol.get(p.symbol)
            if existing is None or p.updated_at > existing.updated_at:
                latest_per_symbol[p.symbol] = p
        return list(latest_per_symbol.values())

    async def read_position_history(
        self,
        symbol: Symbol,
        since: datetime,
    ) -> list[Position]:
        return [p for p in self._positions if p.symbol == symbol and p.updated_at >= since]
