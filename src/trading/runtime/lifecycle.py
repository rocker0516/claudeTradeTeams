"""Lifecycle Protocol — start/stop contract shared by long-running components.

Structural typing (Protocol with @runtime_checkable). Any class that
defines async start() and async stop() satisfies this implicitly —
Venue, MarketDataSource, EventBus, and Supervisor all conform without
explicit inheritance.

Supervisor uses Lifecycle to start and stop components uniformly.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Lifecycle(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
