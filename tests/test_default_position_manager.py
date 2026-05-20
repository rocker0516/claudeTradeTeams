"""DefaultPositionManager contract tests."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading.clock import RealClock
from trading.domain import (
    Balance,
    Fill,
    Order,
    OrderId,
    OrderRequest,
    Position,
    PositionSide,
    Symbol,
)
from trading.persistence.account_journal import InMemoryAccountJournal
from trading.pms import DefaultPositionManager
from trading.venues import Venue


class _FakeVenue(Venue):
    def __init__(self) -> None:
        self.positions_to_return: list[Position] = []
        self.balance_to_return: Balance = Balance(
            "USDT",
            Decimal("1000"),
            Decimal("900"),
            Decimal("100"),
            datetime.now(timezone.utc),
        )
        self.get_positions_calls = 0
        self.get_balance_calls = 0

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    async def place_order(self, request: OrderRequest) -> OrderId:
        raise NotImplementedError

    async def cancel_order(self, order_id: OrderId) -> bool:
        return False

    async def get_order(self, order_id: OrderId) -> Order | None:
        return None

    async def get_open_orders(self, symbol: Symbol | None = None) -> list[Order]:
        return []

    async def get_positions(self) -> list[Position]:
        self.get_positions_calls += 1
        return list(self.positions_to_return)

    async def get_balance(self) -> Balance:
        self.get_balance_calls += 1
        return self.balance_to_return

    def on_order_update(self, callback: Callable[[Order], Awaitable[None]]) -> None: ...
    def on_fill(self, callback: Callable[[Fill], Awaitable[None]]) -> None: ...


def _position(symbol: str, qty: str, now: datetime) -> Position:
    return Position(
        symbol=Symbol(symbol),
        side=PositionSide.LONG,
        quantity=Decimal(qty),
        entry_price=Decimal("60000"),
        mark_price=Decimal("60100"),
        unrealized_pnl=Decimal("50"),
        realized_pnl=Decimal("0"),
        leverage=Decimal("2"),
        liquidation_price=Decimal("30000"),
        updated_at=now,
    )


def _build(
    journal: InMemoryAccountJournal | None = None,
) -> tuple[DefaultPositionManager, _FakeVenue]:
    venue = _FakeVenue()
    pms = DefaultPositionManager(venue=venue, clock=RealClock(), journal=journal)
    return pms, venue


async def test_positions_empty_before_reconcile() -> None:
    pms, _venue = _build()
    assert await pms.positions() == []


async def test_position_by_symbol_returns_none_before_reconcile() -> None:
    pms, _venue = _build()
    assert await pms.position(Symbol("BTCUSDT")) is None


async def test_balance_lazy_fetches_from_venue() -> None:
    pms, venue = _build()
    assert venue.get_balance_calls == 0
    bal = await pms.balance()
    assert venue.get_balance_calls == 1
    assert bal.total == Decimal("1000")
    # Second call uses cache
    await pms.balance()
    assert venue.get_balance_calls == 1


async def test_reconcile_populates_positions_from_venue() -> None:
    pms, venue = _build()
    now = datetime.now(timezone.utc)
    venue.positions_to_return = [
        _position("BTCUSDT", "0.5", now),
        _position("ETHUSDT", "10", now),
    ]
    await pms.reconcile()

    positions = await pms.positions()
    assert {p.symbol for p in positions} == {Symbol("BTCUSDT"), Symbol("ETHUSDT")}


async def test_reconcile_refreshes_balance() -> None:
    pms, venue = _build()
    venue.balance_to_return = Balance(
        "USDT",
        Decimal("5000"),
        Decimal("4500"),
        Decimal("500"),
        datetime.now(timezone.utc),
    )
    await pms.reconcile()

    bal = await pms.balance()
    assert bal.total == Decimal("5000")


async def test_reconcile_replaces_stale_positions() -> None:
    """If venue no longer reports a position, PMS drops it too."""
    pms, venue = _build()
    now = datetime.now(timezone.utc)
    venue.positions_to_return = [_position("BTCUSDT", "1", now)]
    await pms.reconcile()
    assert await pms.position(Symbol("BTCUSDT")) is not None

    # Venue closes the position
    venue.positions_to_return = []
    await pms.reconcile()
    assert await pms.position(Symbol("BTCUSDT")) is None
    assert await pms.positions() == []


async def test_position_by_symbol_after_reconcile() -> None:
    pms, venue = _build()
    now = datetime.now(timezone.utc)
    venue.positions_to_return = [
        _position("BTCUSDT", "0.5", now),
        _position("ETHUSDT", "10", now),
    ]
    await pms.reconcile()

    btc = await pms.position(Symbol("BTCUSDT"))
    assert btc is not None
    assert btc.quantity == Decimal("0.5")

    assert await pms.position(Symbol("SOLUSDT")) is None


async def test_reconcile_writes_to_journal_when_wired() -> None:
    journal = InMemoryAccountJournal()
    pms, venue = _build(journal=journal)
    now = datetime.now(timezone.utc)
    venue.positions_to_return = [_position("BTCUSDT", "0.5", now)]
    await pms.reconcile()

    latest = await journal.read_latest_position(Symbol("BTCUSDT"))
    assert latest is not None
    assert latest.quantity == Decimal("0.5")

    latest_bal = await journal.read_latest_balance()
    assert latest_bal is not None


async def test_no_journal_means_no_persistence() -> None:
    """journal=None is valid — PMS works as pure cache."""
    pms, venue = _build(journal=None)
    now = datetime.now(timezone.utc)
    venue.positions_to_return = [_position("BTCUSDT", "0.5", now)]
    await pms.reconcile()  # should not crash
    assert (await pms.position(Symbol("BTCUSDT"))) is not None


async def test_reconcile_writes_balance_snapshot_to_journal() -> None:
    journal = InMemoryAccountJournal()
    pms, _venue = _build(journal=journal)
    await pms.reconcile()

    from datetime import datetime, timezone

    base = datetime.now(timezone.utc) - timedelta(seconds=10)
    history_via_latest = await journal.read_latest_balance()
    assert history_via_latest is not None
    assert history_via_latest.total == Decimal("1000")
    # base only used to silence import; real check above
    _ = base


# ============================================================
# Restore from journal (Lifecycle start)
# ============================================================


async def test_start_restores_positions_and_balance_from_journal() -> None:
    """Pre-populate journal, build fresh PMS, call start, verify cache populated."""
    journal = InMemoryAccountJournal()
    now = datetime.now(timezone.utc)
    btc = _position("BTCUSDT", "1", now)
    eth = _position("ETHUSDT", "2", now)
    bal = Balance(
        currency="USDT",
        total=Decimal("5000"),
        available=Decimal("4500"),
        margin_used=Decimal("500"),
        updated_at=now,
    )
    await journal.write_position_snapshot(btc)
    await journal.write_position_snapshot(eth)
    await journal.write_balance_snapshot(bal)

    venue = _FakeVenue()  # not used; we call start() not reconcile()
    pms = DefaultPositionManager(venue=venue, clock=RealClock(), journal=journal)
    await pms.start()

    positions = await pms.positions()
    by_symbol = {p.symbol: p for p in positions}
    assert set(by_symbol.keys()) == {Symbol("BTCUSDT"), Symbol("ETHUSDT")}

    restored_bal = await pms.balance()
    assert restored_bal.total == Decimal("5000")
    # balance() did NOT call venue.get_balance because cache is hot
    assert venue.get_balance_calls == 0


async def test_start_with_no_journal_leaves_cache_empty() -> None:
    venue = _FakeVenue()
    pms = DefaultPositionManager(venue=venue, clock=RealClock(), journal=None)
    await pms.start()  # no-op
    assert await pms.positions() == []
    # balance() falls back to venue
    await pms.balance()
    assert venue.get_balance_calls == 1


async def test_start_with_empty_journal_leaves_cache_empty() -> None:
    venue = _FakeVenue()
    journal = InMemoryAccountJournal()
    pms = DefaultPositionManager(venue=venue, clock=RealClock(), journal=journal)
    await pms.start()
    assert await pms.positions() == []
    assert await pms.position(Symbol("BTCUSDT")) is None
