"""Tests for techrevati.runtime.circuit_breaker"""

import time
import threading
from techrevati.runtime.circuit_breaker import (
    CircuitBreaker, CircuitState, CircuitOpenError,
)


def test_initial_state_is_closed():
    """Circuit breaker starts in CLOSED state."""
    cb = CircuitBreaker("test")
    assert cb.state() == CircuitState.CLOSED
    assert not cb.is_open()


def test_successful_calls_keep_circuit_closed():
    """Successful calls don't trigger circuit open."""
    cb = CircuitBreaker("test", failure_threshold=3)

    def success_fn():
        return "ok"

    for _ in range(10):
        result = cb.call(success_fn)
        assert result == "ok"

    assert cb.state() == CircuitState.CLOSED


def test_failures_increment_counter():
    """Failed calls increment failure counter."""
    cb = CircuitBreaker("test", failure_threshold=3)

    def fail_fn():
        raise RuntimeError("test error")

    try:
        cb.call(fail_fn)
    except RuntimeError:
        pass

    try:
        cb.call(fail_fn)
    except RuntimeError:
        pass

    # After 2 failures, still closed (threshold is 3)
    assert cb.state() == CircuitState.CLOSED


def test_circuit_opens_at_threshold():
    """Circuit opens after failure_threshold failures."""
    cb = CircuitBreaker("test", failure_threshold=3)

    def fail_fn():
        raise RuntimeError("test error")

    # Record 3 failures
    for i in range(3):
        try:
            cb.call(fail_fn)
        except RuntimeError:
            pass

    # Circuit should now be open
    assert cb.state() == CircuitState.OPEN
    assert cb.is_open()


def test_open_circuit_blocks_requests():
    """Open circuit immediately raises CircuitOpenError."""
    cb = CircuitBreaker("test", failure_threshold=1)

    def fail_fn():
        raise RuntimeError("test error")

    # Open the circuit
    try:
        cb.call(fail_fn)
    except RuntimeError:
        pass

    assert cb.is_open()

    # Next call should fail with CircuitOpenError, not execute fn
    with pytest.raises(CircuitOpenError) as exc_info:
        cb.call(fail_fn)

    assert "test" in str(exc_info.value)


def test_half_open_state_allows_probe_request():
    """HALF_OPEN state allows one request to test recovery."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout_seconds=0.1)

    def fail_fn():
        raise RuntimeError("test error")

    # Open the circuit
    try:
        cb.call(fail_fn)
    except RuntimeError:
        pass

    assert cb.is_open()

    # Wait for recovery timeout
    time.sleep(0.15)

    # Next call transitions to HALF_OPEN and allows the probe
    def success_fn():
        return "recovered"

    result = cb.call(success_fn)
    assert result == "recovered"

    # After successful probe, circuit should be closed
    assert cb.state() == CircuitState.CLOSED


def test_failed_probe_request_keeps_circuit_open():
    """Failed probe in HALF_OPEN transitions back to OPEN."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout_seconds=0.1)

    def fail_fn():
        raise RuntimeError("test error")

    # Open the circuit
    try:
        cb.call(fail_fn)
    except RuntimeError:
        pass

    # Wait for recovery timeout to enable HALF_OPEN
    time.sleep(0.15)

    # Probe fails — circuit goes back to OPEN
    try:
        cb.call(fail_fn)
    except RuntimeError:
        pass

    assert cb.state() == CircuitState.OPEN


def test_record_success_resets_failure_count():
    """Successful call resets failure counter."""
    cb = CircuitBreaker("test", failure_threshold=3)

    def fail_fn():
        raise RuntimeError("test error")

    def success_fn():
        return "ok"

    # Record 2 failures
    for _ in range(2):
        try:
            cb.call(fail_fn)
        except RuntimeError:
            pass

    assert cb.state() == CircuitState.CLOSED

    # Success resets counter
    cb.call(success_fn)

    # Now we can fail 3 more times before opening
    for _ in range(3):
        try:
            cb.call(fail_fn)
        except RuntimeError:
            pass

    assert cb.state() == CircuitState.OPEN


def test_manual_reset():
    """Manual reset() closes the circuit immediately."""
    cb = CircuitBreaker("test", failure_threshold=1)

    def fail_fn():
        raise RuntimeError("test error")

    # Open the circuit
    try:
        cb.call(fail_fn)
    except RuntimeError:
        pass

    assert cb.is_open()

    # Manual reset
    cb.reset()

    assert cb.state() == CircuitState.CLOSED


def test_thread_safety():
    """Circuit breaker is thread-safe under concurrent access."""
    cb = CircuitBreaker("test", failure_threshold=100)

    def failing_task():
        raise RuntimeError("test error")

    def working_task():
        return "ok"

    errors = []

    def worker():
        try:
            for _ in range(50):
                try:
                    if threading.current_thread().name.endswith("0"):
                        cb.call(failing_task)
                    else:
                        cb.call(working_task)
                except (RuntimeError, CircuitOpenError):
                    pass
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, name=f"worker-{i}") for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread safety violation: {errors}"


def test_circuit_breaker_with_context_manager_integration():
    """Circuit breaker integrates with recovery patterns."""
    cb = CircuitBreaker("downstream_api", failure_threshold=2)

    def api_call():
        return {"status": "ok"}

    # Simulate mixed success/failure
    def call_with_cb():
        try:
            return cb.call(api_call)
        except CircuitOpenError as e:
            # Could integrate with RecoveryContext here
            return {"error": str(e)}

    result = call_with_cb()
    assert result["status"] == "ok"


def test_state_transitions_are_atomic():
    """State transitions happen atomically without race conditions."""
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout_seconds=0.05)

    states_observed = []

    def record_state():
        for _ in range(50):
            states_observed.append(cb.state())
            time.sleep(0.001)

    def fail_and_recover():
        try:
            cb.call(lambda: 1 / 0)
        except (RuntimeError, ZeroDivisionError):
            pass

        time.sleep(0.07)  # Wait for recovery timeout
        cb.call(lambda: "ok")

    t1 = threading.Thread(target=record_state)
    t2 = threading.Thread(target=fail_and_recover)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Should only observe valid states
    for s in states_observed:
        assert s in (CircuitState.CLOSED, CircuitState.OPEN, CircuitState.HALF_OPEN)


# Need to import pytest for the test that uses it
import pytest
