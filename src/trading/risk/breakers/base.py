"""Abstract CircuitBreaker.

A single safety check that examines a RiskContext and returns either
None (no trip) or a Trigger describing what action the Supervisor
should take.

Two flavors of concrete breaker:

  - Stateless: pure function over RiskContext (LeverageBreaker,
    PositionNotionalBreaker, FundingSpikeBreaker, LiquidationBreaker).
    All inputs come from the context.

  - Stateful: maintains counters / history via EventBus subscriptions
    set up in __init__ (ConsecutiveErrorBreaker, HeartbeatBreaker,
    DrawdownBreaker with rolling window). evaluate() just reads its
    internal state — no I/O.

evaluate() is synchronous. If a breaker needs external data, fetch
it once upfront when the breaker is constructed, or subscribe to
events to accumulate it — don't do I/O in evaluate().

Latching is per-breaker policy. Some breakers should stay tripped
once fired until human ack (drawdown); others should clear when the
condition clears (heartbeat returns). The ABC doesn't impose either —
the breaker decides whether to return a Trigger on the next tick.
"""

from abc import ABC, abstractmethod

from ...domain import RiskContext, Trigger


class CircuitBreaker(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for this breaker. Used in Trigger.breaker_name
        and BreakerTripped events. Must be unique within a RiskManager."""
        ...

    @abstractmethod
    def evaluate(self, context: RiskContext) -> Trigger | None:
        """Return a Trigger if this breaker should fire given the context,
        otherwise None. Must be side-effect free w.r.t. external systems —
        no I/O, no event publishing. Internal counter updates are allowed
        but typically belong in event subscribers, not here."""
        ...
