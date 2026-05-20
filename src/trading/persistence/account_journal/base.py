"""Abstract AccountJournal — append-only persistence for positions + balance.

Periodic snapshots, not incremental events. PMS writes a snapshot
when state changes (after a fill) and on a periodic cadence (every N
minutes). Reading "latest" returns the most recent snapshot per
symbol / currency.

On restart, PMS reads the latest snapshot per symbol, applies fills
since that snapshot via OrderJournal.read_fills_since(), then
reconciles against the venue.

Schema sketch::

  position_snapshots:
    id (pk), symbol (indexed), side, quantity, entry_price, mark_price,
    unrealized_pnl, realized_pnl, leverage, liquidation_price,
    updated_at (indexed; latest-per-symbol queries use this)

  balance_snapshots:
    id (pk), currency, total, available, margin_used,
    updated_at (indexed)

Append-only.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from ...domain import Balance, Position, Symbol


class AccountJournal(ABC):
    @abstractmethod
    async def write_position_snapshot(self, position: Position) -> None: ...

    @abstractmethod
    async def write_balance_snapshot(self, balance: Balance) -> None: ...

    @abstractmethod
    async def read_latest_position(self, symbol: Symbol) -> Position | None:
        """Return the most recent position snapshot for `symbol`, or
        None if no snapshot exists."""
        ...

    @abstractmethod
    async def read_latest_balance(self) -> Balance | None:
        """Return the most recent balance snapshot, or None if none exists."""
        ...

    @abstractmethod
    async def read_all_latest_positions(self) -> list[Position]:
        """Return the most recent snapshot for every symbol that has any
        snapshot history. Used by PMS.start() to restore the cache on
        restart without needing to know which symbols to query for."""
        ...

    @abstractmethod
    async def read_position_history(
        self,
        symbol: Symbol,
        since: datetime,
    ) -> list[Position]:
        """Return all snapshots for `symbol` since `since`, ascending."""
        ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...
