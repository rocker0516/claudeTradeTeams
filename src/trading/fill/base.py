"""Abstract FillEngine.

Used by PaperVenue and BacktestVenue to simulate order fills against
a book snapshot. Live venues do NOT use a FillEngine — the real
exchange fills orders and the result arrives via the private channel.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from ..domain import Fill, Order, OrderbookSnapshot


class FillEngine(ABC):
    @abstractmethod
    def try_fill(
        self,
        order: Order,
        book: OrderbookSnapshot,
        timestamp: datetime,
    ) -> Fill | None:
        """Attempt to fill `order` against `book` at `timestamp`.

        Returns None if the order should remain open (e.g. limit price
        not crossed). Returns a Fill which may be partial — the caller
        updates Order.filled_quantity and may invoke try_fill again on
        subsequent ticks.
        """
        ...
