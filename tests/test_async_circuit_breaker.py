"""Tests for techrevati.runtime.circuit_breaker.AsyncCircuitBreaker"""

from __future__ import annotations

import asyncio

import pytest

from techrevati.runtime.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


class ManualClock:
    """Test double for an injectable monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.mark.asyncio
async def test_initial_state_is_closed():
    cb = AsyncCircuitBreaker("test")
    assert await cb.state() == CircuitState.CLOSED
    assert not await cb.is_open()


@pytest.mark.asyncio
async def test_successful_calls_keep_closed():
    cb = AsyncCircuitBreaker("test", failure_threshold=3)

    async def ok():
        return "ok"

    for _ in range(10):
        assert await cb.call(ok) == "ok"
    assert await cb.state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_failures_open_circuit_at_threshold():
    cb = AsyncCircuitBreaker("test", failure_threshold=3)

    async def boom():
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(boom)

    assert await cb.is_open()


@pytest.mark.asyncio
async def test_half_open_after_recovery_window():
    clock = ManualClock()
    cb = AsyncCircuitBreaker(
        "test", failure_threshold=1, recovery_timeout_seconds=0.1, clock=clock
    )

    async def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(boom)

    clock.advance(0.2)

    async def ok():
        return "recovered"

    assert await cb.call(ok) == "recovered"
    assert await cb.state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_failed_probe_returns_to_open():
    clock = ManualClock()
    cb = AsyncCircuitBreaker(
        "test", failure_threshold=1, recovery_timeout_seconds=0.1, clock=clock
    )

    async def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(boom)

    clock.advance(0.2)

    with pytest.raises(RuntimeError):
        await cb.call(boom)

    assert await cb.state() == CircuitState.OPEN


@pytest.mark.asyncio
async def test_half_open_serializes_probes_default():
    """half_open_max_probes=1: only one concurrent probe is admitted."""
    clock = ManualClock()
    cb = AsyncCircuitBreaker(
        "svc",
        failure_threshold=1,
        recovery_timeout_seconds=0.1,
        half_open_max_probes=1,
        clock=clock,
    )

    async def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(boom)

    clock.advance(0.2)

    release = asyncio.Event()
    started = asyncio.Event()
    invocations = 0

    async def slow_probe():
        nonlocal invocations
        invocations += 1
        started.set()
        await release.wait()
        return "ok"

    async def quick():
        return "quick"

    probe_task = asyncio.create_task(cb.call(slow_probe))
    await started.wait()

    rejected = 0
    for _ in range(10):
        try:
            await cb.call(quick)
        except CircuitOpenError:
            rejected += 1

    release.set()
    await probe_task

    assert invocations == 1
    assert rejected == 10
    assert await cb.state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_manual_reset():
    cb = AsyncCircuitBreaker("test", failure_threshold=1)

    async def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(boom)

    assert await cb.is_open()
    await cb.reset()
    assert await cb.state() == CircuitState.CLOSED
