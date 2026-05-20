"""RealClock contract tests."""

import time
from datetime import timezone

from trading.clock import RealClock


def test_real_clock_now_returns_utc_aware() -> None:
    clock = RealClock()
    t = clock.now()
    assert t.tzinfo == timezone.utc


def test_real_clock_monotonic_never_decreases() -> None:
    clock = RealClock()
    t1 = clock.monotonic()
    t2 = clock.monotonic()
    assert t2 >= t1


async def test_real_clock_sleep_delays() -> None:
    clock = RealClock()
    start = time.monotonic()
    await clock.sleep(0.05)
    elapsed = time.monotonic() - start
    # Allow scheduler jitter, but verify it didn't return immediately
    assert elapsed >= 0.04
    assert elapsed < 1.0
