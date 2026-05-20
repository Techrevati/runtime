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
import time
from datetime import UTC, datetime
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
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        await asyncio.sleep(seconds)

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
        self._t = float(start)
        self._wall = wall_start or datetime(2026, 1, 1, tzinfo=UTC)

    # Sync entry points -------------------------------------------------

    def monotonic(self) -> float:
        return self._t

    def wall_now(self) -> datetime:
        return self._wall

    def __call__(self) -> float:
        return self._t

    # Mutation helpers --------------------------------------------------

    def advance(self, seconds: float) -> None:
        """Move both clocks forward by ``seconds``."""
        from datetime import timedelta

        self._t += float(seconds)
        self._wall = self._wall + timedelta(seconds=float(seconds))

    def tick(self, absolute_monotonic: float) -> None:
        """Set monotonic to a specific value (must be non-decreasing)."""
        if absolute_monotonic < self._t:
            raise ValueError(
                f"ManualClock cannot move backwards: {absolute_monotonic} < {self._t}"
            )
        delta = absolute_monotonic - self._t
        self.advance(delta)

    def now_utc(self) -> datetime:
        return self._wall

    # Async helper ------------------------------------------------------

    async def sleep_async(self, seconds: float) -> None:
        # Yield once so cooperative awaiters get scheduled, then bump
        # the simulated clock. Real wall time does not pass.
        await asyncio.sleep(0)
        if seconds > 0:
            self.advance(seconds)
