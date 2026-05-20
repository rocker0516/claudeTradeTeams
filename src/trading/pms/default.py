"""DefaultPositionManager — venue-truth cache, refreshed on reconcile().

Design choice: this PMS does NOT subscribe to Venue fill events. Position
state is refreshed only by `reconcile()` — typically called periodically
by Supervisor's tick loop. Between reconciles the cache is stale.

Trade-off: incremental fill→position math (multi-direction netting,
weighted avg entry price, realized PnL on reduction) is non-trivial.
For Phase 0 POC, simple periodic reconciliation is preferred —
correctness over latency. A future PMS impl can add incremental updates
on top of this same cache pattern.

Optional `journal` writes position + balance snapshots on each reconcile
for restart recovery.
"""

from ..clock import Clock
from ..domain import Balance, Position, Symbol
from ..persistence.account_journal import AccountJournal
from ..venues import Venue
from .base import PositionManager


class DefaultPositionManager(PositionManager):
    def __init__(
        self,
        *,
        venue: Venue,
        clock: Clock,
        journal: AccountJournal | None = None,
    ) -> None:
        self._venue = venue
        self._clock = clock
        self._journal = journal
        self._positions: dict[Symbol, Position] = {}
        self._balance: Balance | None = None

    # ===== Lifecycle (structural — matches Lifecycle Protocol) =====

    async def start(self) -> None:
        """Restore positions + balance cache from the journal.

        Best-effort: if no journal is wired or it has no snapshots, the
        cache stays empty. After Supervisor's initial reconcile, this
        restored state is overwritten by venue truth — the journal
        restore exists as a fallback if the first venue call fails.
        """
        if self._journal is None:
            return
        self._balance = await self._journal.read_latest_balance()
        positions = await self._journal.read_all_latest_positions()
        self._positions = {p.symbol: p for p in positions}

    async def stop(self) -> None:
        """No-op — cache is in-memory."""

    async def positions(self) -> list[Position]:
        return list(self._positions.values())

    async def position(self, symbol: Symbol) -> Position | None:
        return self._positions.get(symbol)

    async def balance(self) -> Balance:
        if self._balance is None:
            # First access — lazy-fetch from venue rather than returning
            # a stub zero-balance that could mislead risk checks
            self._balance = await self._venue.get_balance()
        return self._balance

    async def reconcile(self) -> None:
        """Full refresh from venue. Replaces cached state entirely.

        Persists snapshots if journal is wired.
        """
        positions = await self._venue.get_positions()
        self._positions = {p.symbol: p for p in positions}
        self._balance = await self._venue.get_balance()

        if self._journal is not None:
            for pos in self._positions.values():
                await self._journal.write_position_snapshot(pos)
            await self._journal.write_balance_snapshot(self._balance)
