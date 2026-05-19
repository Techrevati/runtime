"""
Circuit Breaker — Fault-tolerant execution with state machine.

Implements the Circuit Breaker pattern to prevent cascading failures
when calling unreliable services or operations. Transitions between
CLOSED (normal), OPEN (failing), and HALF_OPEN (testing) states.

Thread-safe with configurable failure threshold and recovery timeout.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker lifecycle states."""

    CLOSED = "closed"  # Normal operation; requests pass through
    OPEN = "open"  # Failed; requests blocked immediately
    HALF_OPEN = "half_open"  # Testing; one request allowed to probe


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and request is blocked."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is OPEN")


@dataclass
class CircuitBreaker:
    """Stateful circuit breaker with thread-safe transitions."""

    name: str
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute fn with breaker protection. Raises CircuitOpenError if open."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                else:
                    raise CircuitOpenError(self.name)

        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def record_success(self) -> None:
        """Record a successful execution. Closes the circuit if HALF_OPEN."""
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed execution. Opens the circuit at threshold."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            if self._state == CircuitState.OPEN and self._should_attempt_reset():
                return CircuitState.HALF_OPEN
            return self._state

    def is_open(self) -> bool:
        """Return True if circuit is open (blocking requests)."""
        return self.state() == CircuitState.OPEN

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None

    def _should_attempt_reset(self) -> bool:
        """Check if recovery timeout has elapsed since last failure."""
        if self._last_failure_time is None:
            return False
        return (time.time() - self._last_failure_time) >= self.recovery_timeout_seconds
