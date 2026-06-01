"""
Scheduler / clock abstraction.

Every primitive in the runtime that depends on time accepts a clock
function (``Callable[[], float]`` returning monotonic seconds). The
``Clock`` protocol below formalizes that contract and adds two helpers
production code occasionally needs:

- ``wall_now`` for emitting ISO-8601 timestamps without re-wiring
  ``datetime.now``;
- ``sleep_async`` as a single hook tests can replace to keep async
  suites fast.

``ManualClock`` is the canonical test double. It used to live in
``tests/conftest.py`` only; promoting it makes the contract reusable
in downstream test suites that exercise our primitives.

The functions are intentionally synchronous; the async helper sits
alongside as a separate method instead of forcing every clock to be
async.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

__all__ = [
    "Clock",
    "ManualClock",
    "SystemClock",
]


@runtime_checkable
class Clock(Protocol):
    """Injectable time source.

    Implementations must be safe for concurrent calls from multiple
    threads / tasks. The default ``SystemClock`` is stateless and
    obviously safe; ``ManualClock`` uses a lock so tests that advance
    time from a sibling thread don't race.
    """

    def monotonic(self) -> float:
        """Seconds since an arbitrary fixed epoch; never decreases."""
        ...

    def wall_now(self) -> datetime:
        """Calendar time as a UTC-aware ``datetime``."""
        ...

    async def sleep_async(self, seconds: float) -> None:
        """Cooperative wait for the given duration."""
        ...


class SystemClock:
    """Default production clock — wraps stdlib time functions."""

    def monotonic(self) -> float:
        return time.monotonic()

    def wall_now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_async(self, seconds: float) -> None:
        duration = _validate_duration("seconds", seconds)
        if duration == 0:
            await asyncio.sleep(0)
            return
        await asyncio.sleep(duration)

    def __call__(self) -> float:
        """Compatibility shim — many existing primitives accept a
        ``Callable[[], float]`` clock; ``SystemClock`` works wherever
        such a callable is expected.
        """
        return self.monotonic()


class ManualClock:
    """Deterministic clock for tests.

    ``monotonic`` returns the same value until ``advance`` /
    ``tick`` move it forward. ``wall_now`` keeps a parallel calendar
    timestamp so tests that round-trip ISO strings have stable output.
    ``sleep_async`` does not actually sleep — it yields control to the
    event loop once via ``asyncio.sleep(0)`` so awaiting tasks make
    progress, and bumps the monotonic clock by ``seconds`` so the
    primitive under test sees time pass.
    """

    def __init__(
        self,
        start: float = 1000.0,
        wall_start: datetime | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._t = _validate_finite_float("start", start)
        if wall_start is not None:
            self._wall = _validate_wall_start(wall_start)
        else:
            self._wall = datetime(2026, 1, 1, tzinfo=UTC)

    # Sync entry points -------------------------------------------------

    def monotonic(self) -> float:
        with self._lock:
            return self._t

    def wall_now(self) -> datetime:
        with self._lock:
            return self._wall

    def __call__(self) -> float:
        return self.monotonic()

    # Mutation helpers --------------------------------------------------

    def advance(self, seconds: float) -> None:
        """Move both clocks forward by ``seconds``."""
        duration = _validate_duration("seconds", seconds)
        with self._lock:
            self._t += duration
            self._wall = self._wall + timedelta(seconds=duration)

    def tick(self, absolute_monotonic: float) -> None:
        """Set monotonic to a specific value (must be non-decreasing)."""
        target = _validate_finite_float("absolute_monotonic", absolute_monotonic)
        with self._lock:
            if target < self._t:
                raise ValueError(
                    f"ManualClock cannot move backwards: {target} < {self._t}"
                )
            delta = target - self._t
            self._t = target
            self._wall = self._wall + timedelta(seconds=delta)

    def now_utc(self) -> datetime:
        return self.wall_now()

    # Async helper ------------------------------------------------------

    async def sleep_async(self, seconds: float) -> None:
        # Yield once so cooperative awaiters get scheduled, then bump
        # the simulated clock. Real wall time does not pass.
        duration = _validate_duration("seconds", seconds)
        await asyncio.sleep(0)
        if duration > 0:
            self.advance(duration)


def _validate_finite_float(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _validate_duration(field_name: str, value: float) -> float:
    duration = _validate_finite_float(field_name, value)
    if duration < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return duration


def _validate_wall_start(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("wall_start must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("wall_start must be timezone-aware")
    return value.astimezone(UTC)
