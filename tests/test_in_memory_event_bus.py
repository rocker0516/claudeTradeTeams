"""InMemoryEventBus contract tests."""

from dataclasses import dataclass

import pytest

from trading.runtime.event_bus import InMemoryEventBus


@dataclass(frozen=True)
class _BaseEvent:
    payload: str


@dataclass(frozen=True)
class _ChildEvent(_BaseEvent):
    extra: int = 0


@dataclass(frozen=True)
class _OtherEvent:
    name: str


async def test_subscribe_and_publish_invokes_callback() -> None:
    bus = InMemoryEventBus()
    received: list[_BaseEvent] = []

    async def cb(event: _BaseEvent) -> None:
        received.append(event)

    bus.subscribe(_BaseEvent, cb)
    await bus.start()
    await bus.publish(_BaseEvent("hello"))

    assert len(received) == 1
    assert received[0].payload == "hello"


async def test_multiple_subscribers_all_invoked() -> None:
    bus = InMemoryEventBus()
    cb1_calls: list[_BaseEvent] = []
    cb2_calls: list[_BaseEvent] = []

    async def cb1(e: _BaseEvent) -> None:
        cb1_calls.append(e)

    async def cb2(e: _BaseEvent) -> None:
        cb2_calls.append(e)

    bus.subscribe(_BaseEvent, cb1)
    bus.subscribe(_BaseEvent, cb2)
    await bus.start()
    await bus.publish(_BaseEvent("hi"))

    assert len(cb1_calls) == 1
    assert len(cb2_calls) == 1


async def test_isinstance_dispatch_to_parent_subscriber() -> None:
    """Subscriber on a base class also receives subclass events."""
    bus = InMemoryEventBus()
    received: list[_BaseEvent] = []

    async def cb(e: _BaseEvent) -> None:
        received.append(e)

    bus.subscribe(_BaseEvent, cb)
    await bus.start()
    await bus.publish(_ChildEvent("subclass", extra=42))

    assert len(received) == 1
    assert isinstance(received[0], _ChildEvent)


async def test_dispatch_skips_unrelated_types() -> None:
    bus = InMemoryEventBus()
    received: list[_BaseEvent] = []

    async def cb(e: _BaseEvent) -> None:
        received.append(e)

    bus.subscribe(_BaseEvent, cb)
    await bus.start()
    await bus.publish(_OtherEvent("unrelated"))

    assert received == []


async def test_subscriber_exception_does_not_break_dispatch() -> None:
    bus = InMemoryEventBus()
    received: list[_BaseEvent] = []

    async def failing(_e: _BaseEvent) -> None:
        raise RuntimeError("boom")

    async def good(e: _BaseEvent) -> None:
        received.append(e)

    bus.subscribe(_BaseEvent, failing)
    bus.subscribe(_BaseEvent, good)
    await bus.start()
    await bus.publish(_BaseEvent("x"))

    assert len(received) == 1


async def test_publish_before_start_raises() -> None:
    bus = InMemoryEventBus()
    with pytest.raises(RuntimeError, match="not started"):
        await bus.publish(_BaseEvent("x"))


async def test_publish_after_stop_raises() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    await bus.stop()
    with pytest.raises(RuntimeError, match="stopped"):
        await bus.publish(_BaseEvent("x"))


async def test_subscribe_after_stop_raises() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    await bus.stop()

    async def cb(_e: _BaseEvent) -> None: ...

    with pytest.raises(RuntimeError, match="stopped"):
        bus.subscribe(_BaseEvent, cb)


async def test_start_after_stop_raises() -> None:
    bus = InMemoryEventBus()
    await bus.start()
    await bus.stop()
    with pytest.raises(RuntimeError, match="already stopped"):
        await bus.start()
