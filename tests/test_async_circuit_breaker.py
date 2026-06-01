"""Tests for techrevati.runtime.circuit_breaker.AsyncCircuitBreaker"""

from __future__ import annotations

import asyncio

import pytest

from techrevati.runtime.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitOpenError,
    CircuitState,
)
from tests.conftest import ManualClock


@pytest.mark.asyncio
async def test_initial_state_is_closed():
    cb = AsyncCircuitBreaker("test")
    assert await cb.state() == CircuitState.CLOSED
    assert not await cb.is_open()


def test_constructor_rejects_invalid_params():
    with pytest.raises(ValueError, match="failure_threshold"):
        AsyncCircuitBreaker("test", failure_threshold=0)
    with pytest.raises(ValueError, match="recovery_timeout_seconds"):
        AsyncCircuitBreaker("test", recovery_timeout_seconds=-0.1)
    with pytest.raises(ValueError, match="half_open_max_probes"):
        AsyncCircuitBreaker("test", half_open_max_probes=0)


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


@pytest.mark.asyncio
async def test_half_open_closes_only_after_all_inflight_probes_succeed():
    clock = ManualClock()
    cb = AsyncCircuitBreaker(
        "svc",
        failure_threshold=3,
        recovery_timeout_seconds=0.1,
        half_open_max_probes=2,
        clock=clock,
    )

    async def boom():
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(boom)
    clock.advance(0.2)

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    first_release = asyncio.Event()
    second_release = asyncio.Event()

    async def first_probe():
        first_started.set()
        await first_release.wait()
        return "first-ok"

    async def second_probe():
        second_started.set()
        await second_release.wait()
        return "second-ok"

    first = asyncio.create_task(cb.call(first_probe))
    second = asyncio.create_task(cb.call(second_probe))
    await first_started.wait()
    await second_started.wait()

    first_release.set()
    await first

    assert await cb.state() == CircuitState.HALF_OPEN
    async with cb._lock:
        assert cb._probe_in_flight == 1

    second_release.set()
    await second

    assert await cb.state() == CircuitState.CLOSED
    async with cb._lock:
        assert cb._probe_in_flight == 0


@pytest.mark.asyncio
async def test_half_open_failing_sibling_reopens_after_successful_sibling():
    clock = ManualClock()
    cb = AsyncCircuitBreaker(
        "svc",
        failure_threshold=3,
        recovery_timeout_seconds=0.1,
        half_open_max_probes=2,
        clock=clock,
    )

    async def boom():
        raise RuntimeError("boom")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(boom)
    clock.advance(0.2)

    ok_started = asyncio.Event()
    fail_started = asyncio.Event()
    ok_release = asyncio.Event()
    fail_release = asyncio.Event()

    async def ok_probe():
        ok_started.set()
        await ok_release.wait()
        return "ok"

    async def failing_probe():
        fail_started.set()
        await fail_release.wait()
        raise RuntimeError("still down")

    ok_task = asyncio.create_task(cb.call(ok_probe))
    fail_task = asyncio.create_task(cb.call(failing_probe))
    await ok_started.wait()
    await fail_started.wait()

    ok_release.set()
    assert await ok_task == "ok"
    assert await cb.state() == CircuitState.HALF_OPEN

    fail_release.set()
    with pytest.raises(RuntimeError):
        await fail_task

    assert await cb.state() == CircuitState.OPEN


@pytest.mark.asyncio
async def test_cancelled_half_open_probe_releases_permit():
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

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_probe():
        started.set()
        await release.wait()
        return "late"

    probe = asyncio.create_task(cb.call(slow_probe))
    await started.wait()
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    assert await cb.state() == CircuitState.HALF_OPEN
    async with cb._lock:
        assert cb._probe_in_flight == 0

    async def ok():
        return "recovered"

    assert await cb.call(ok) == "recovered"
    assert await cb.state() == CircuitState.CLOSED
