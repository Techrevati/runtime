"""Shared test fixtures and utilities.

ManualClock is the test double for any primitive that accepts an injectable
monotonic clock (currently CircuitBreaker / AsyncCircuitBreaker via their
``clock`` field, and any future rate-limiter or scheduler with the same
contract). Using a single source-of-truth class keeps the contract uniform
across modules and lets Sprint 8 primitives plug into it without re-inventing
the type.
"""

from __future__ import annotations

import pytest


class ManualClock:
    """Test double for an injectable monotonic clock.

    Returns the same value on every call until ``advance()`` is called.
    Construct with ``start`` if a non-zero baseline is needed.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture
def manual_clock() -> ManualClock:
    """Pytest fixture form of ManualClock for tests that prefer DI."""
    return ManualClock()
