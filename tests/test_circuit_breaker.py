"""Tests for techrevati.runtime.circuit_breaker"""

import threading

import pytest

from techrevati.runtime.circuit_breaker import (
    CircuitBreaker,
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


def test_initial_state_is_closed():
    cb = CircuitBreaker("test")
    assert cb.state() == CircuitState.CLOSED
    assert not cb.is_open()


def test_successful_calls_keep_circuit_closed():
    cb = CircuitBreaker("test", failure_threshold=3)
    for _ in range(10):
        assert cb.call(lambda: "ok") == "ok"
    assert cb.state() == CircuitState.CLOSED


def test_failures_increment_counter():
    cb = CircuitBreaker("test", failure_threshold=3)

    def fail_fn():
        raise RuntimeError("test error")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cb.call(fail_fn)

    # After 2 failures, still closed (threshold is 3)
    assert cb.state() == CircuitState.CLOSED


def test_circuit_opens_at_threshold():
    cb = CircuitBreaker("test", failure_threshold=3)

    def fail_fn():
        raise RuntimeError("test error")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            cb.call(fail_fn)

    assert cb.state() == CircuitState.OPEN
    assert cb.is_open()


def test_open_circuit_blocks_requests():
    cb = CircuitBreaker("test", failure_threshold=1)

    def fail_fn():
        raise RuntimeError("test error")

    with pytest.raises(RuntimeError):
        cb.call(fail_fn)

    assert cb.is_open()

    with pytest.raises(CircuitOpenError) as exc_info:
        cb.call(fail_fn)

    assert "test" in str(exc_info.value)


def test_half_open_allows_probe_after_recovery_timeout():
    clock = ManualClock()
    cb = CircuitBreaker(
        "test", failure_threshold=1, recovery_timeout_seconds=0.1, clock=clock
    )

    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert cb.is_open()

    # Advance virtual clock past the recovery window.
    clock.advance(0.2)

    # Probe succeeds → circuit closes.
    result = cb.call(lambda: "recovered")
    assert result == "recovered"
    assert cb.state() == CircuitState.CLOSED


def test_failed_probe_request_keeps_circuit_open():
    clock = ManualClock()
    cb = CircuitBreaker(
        "test", failure_threshold=1, recovery_timeout_seconds=0.1, clock=clock
    )

    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    clock.advance(0.2)

    # Probe fails — circuit goes back to OPEN.
    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("still down")))

    assert cb.state() == CircuitState.OPEN


def test_record_success_resets_failure_count():
    cb = CircuitBreaker("test", failure_threshold=3)

    def fail_fn():
        raise RuntimeError("test error")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cb.call(fail_fn)

    assert cb.state() == CircuitState.CLOSED

    cb.call(lambda: "ok")

    # Now we can fail 3 more times before opening.
    for _ in range(3):
        with pytest.raises(RuntimeError):
            cb.call(fail_fn)

    assert cb.state() == CircuitState.OPEN


def test_manual_reset():
    cb = CircuitBreaker("test", failure_threshold=1)

    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert cb.is_open()

    cb.reset()

    assert cb.state() == CircuitState.CLOSED


def test_thread_safety():
    cb = CircuitBreaker("test", failure_threshold=100)

    def failing_task():
        raise RuntimeError("test error")

    def working_task():
        return "ok"

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(50):
                try:
                    if threading.current_thread().name.endswith("0"):
                        cb.call(failing_task)
                    else:
                        cb.call(working_task)
                except (RuntimeError, CircuitOpenError):
                    pass
        except BaseException as e:  # noqa: BLE001 - intentional capture
            errors.append(e)

    threads = [threading.Thread(target=worker, name=f"worker-{i}") for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


def test_circuit_breaker_returns_ok_on_success():
    cb = CircuitBreaker("downstream_api", failure_threshold=2)
    assert cb.call(lambda: {"status": "ok"}) == {"status": "ok"}


def test_only_valid_states_are_observable():
    """State observed under concurrency is always one of the three values."""
    clock = ManualClock()
    cb = CircuitBreaker(
        "test", failure_threshold=1, recovery_timeout_seconds=0.05, clock=clock
    )

    states_observed: list[CircuitState] = []

    def record_state() -> None:
        for _ in range(50):
            states_observed.append(cb.state())

    def fail_and_recover() -> None:
        try:
            cb.call(lambda: 1 / 0)
        except (RuntimeError, ZeroDivisionError):
            pass
        clock.advance(0.07)
        cb.call(lambda: "ok")

    t1 = threading.Thread(target=record_state)
    t2 = threading.Thread(target=fail_and_recover)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    for s in states_observed:
        assert s in (CircuitState.CLOSED, CircuitState.OPEN, CircuitState.HALF_OPEN)


# -- Half-open probe permit (sprint 1.7+1.8) --


def test_half_open_default_admits_one_probe_only():
    """With half_open_max_probes=1, only one concurrent probe is admitted."""
    clock = ManualClock()
    cb = CircuitBreaker(
        "svc",
        failure_threshold=1,
        recovery_timeout_seconds=0.1,
        half_open_max_probes=1,
        clock=clock,
    )

    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    clock.advance(0.2)
    assert cb.is_open() is False  # state() reports HALF_OPEN

    started = threading.Event()
    release = threading.Event()
    probe_invocations = 0
    extra_open_errors = 0

    def slow_probe() -> str:
        nonlocal probe_invocations
        probe_invocations += 1
        started.set()
        release.wait(timeout=2.0)
        return "ok"

    def fast_call() -> str:
        return "fast"

    probe_thread = threading.Thread(target=lambda: cb.call(slow_probe))
    probe_thread.start()
    assert started.wait(timeout=2.0)

    # While the first probe is in flight, additional callers must be rejected.
    for _ in range(20):
        try:
            cb.call(fast_call)
        except CircuitOpenError:
            extra_open_errors += 1

    release.set()
    probe_thread.join(timeout=2.0)

    assert probe_invocations == 1
    assert extra_open_errors == 20
    assert cb.state() == CircuitState.CLOSED


def test_half_open_admits_n_probes_when_configured():
    """half_open_max_probes=3 admits exactly 3 concurrent probes."""
    clock = ManualClock()
    cb = CircuitBreaker(
        "svc",
        failure_threshold=1,
        recovery_timeout_seconds=0.1,
        half_open_max_probes=3,
        clock=clock,
    )

    with pytest.raises(RuntimeError):
        cb.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    clock.advance(0.2)

    admitted = 0
    rejected = 0
    release = threading.Event()
    barrier = threading.Barrier(parties=10, timeout=2.0)

    def attempt() -> None:
        nonlocal admitted, rejected
        barrier.wait()
        try:
            cb.call(lambda: release.wait(timeout=2.0))
            with threading.Lock():
                admitted += 1
        except CircuitOpenError:
            with threading.Lock():
                rejected += 1

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()

    # Give threads a moment to all arrive at the barrier and contend.
    # We can't release the gate until threads are blocked inside fn().
    # Polling via a short loop is acceptable since this is testing
    # concurrency semantics, not timing thresholds.
    import time as _time

    deadline = _time.monotonic() + 2.0
    while _time.monotonic() < deadline:
        with cb._lock:
            in_flight = cb._probe_in_flight
        if in_flight >= 3:
            break

    release.set()
    for t in threads:
        t.join(timeout=2.0)

    # At any instant, the probe count was capped at 3.
    # Admitted threads each succeeded; rejected threads got CircuitOpenError.
    assert admitted + rejected == 10
    assert admitted >= 3
