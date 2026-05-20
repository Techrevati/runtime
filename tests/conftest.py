"""Shared test fixtures and utilities.

``ManualClock`` is the deterministic test double for any primitive that
accepts an injectable monotonic clock. As of 0.2.0 the canonical
implementation lives in ``techrevati.runtime.scheduler`` so downstream
test suites can use the same class without depending on our
``tests/`` package. We re-export it here so existing imports keep
working unchanged.
"""

from __future__ import annotations

import pytest

from techrevati.runtime import ManualClock

__all__ = ["ManualClock", "manual_clock"]


@pytest.fixture
def manual_clock() -> ManualClock:
    """Pytest fixture form of ManualClock for tests that prefer DI."""
    return ManualClock()
