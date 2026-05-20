"""
Circuit Breaker — Fault-tolerant execution with state machine.

Implements the Circuit Breaker pattern to prevent cascading failures
when calling unreliable services or operations. Transitions between
CLOSED (normal), OPEN (failing), and HALF_OPEN (testing) states.

Thread-safe with configurable failure threshold, recovery timeout,
and number of in-flight probes permitted in HALF_OPEN. Uses
``time.monotonic`` for duration checks so clock jumps don't affect
behavior; the clock function is injectable for deterministic tests.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker lifecycle states."""

    CLOSED = "closed"  # Normal operation; requests pass through
    OPEN = "open"  # Failed; requests blocked immediately
    HALF_OPEN = "half_open"  # Testing; limited probes allowed


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and request is blocked."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is OPEN")


@dataclass
class CircuitBreaker:
    """Stateful circuit breaker with thread-safe transitions.

    Parameters
    ----------
    name:
        Human-readable identifier (included in ``CircuitOpenError``).
    failure_threshold:
        Consecutive failures before the circuit opens.
    recovery_timeout_seconds:
        Duration the circuit stays open before allowing probes.
    half_open_max_probes:
        Concurrent probe calls allowed in HALF_OPEN. Default 1 (Polly
        convention); raising to N spreads recovery risk over multiple
        in-flight calls (Resilience4j defaults to 10).
    clock:
        Monotonic time source. Defaults to ``time.monotonic``. Override
        in tests to make timing-dependent behavior deterministic.
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    half_open_max_probes: int = 1
    clock: Callable[[], float] = field(default=time.monotonic)

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float | None = field(default=None, init=False, repr=False)
    _probe_in_flight: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute fn with breaker protection. Raises CircuitOpenError if open.

        In HALF_OPEN state, at most ``half_open_max_probes`` concurrent
        calls are admitted; excess callers receive ``CircuitOpenError``
        until in-flight probes complete.
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    self._probe_in_flight = 1
                else:
                    raise CircuitOpenError(self.name)
            elif self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight >= self.half_open_max_probes:
                    raise CircuitOpenError(self.name)
                self._probe_in_flight += 1
            # CLOSED: pass through without tracking probes.

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
                self._probe_in_flight = 0

    def record_failure(self) -> None:
        """Record a failed execution. Opens the circuit at threshold."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = self.clock()
            if self._state == CircuitState.HALF_OPEN:
                # Failed probe → back to OPEN, drop all in-flight permits.
                self._state = CircuitState.OPEN
                self._probe_in_flight = 0
            elif self._failure_count >= self.failure_threshold:
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
            self._probe_in_flight = 0

    def _should_attempt_reset(self) -> bool:
        """Check if recovery timeout has elapsed since last failure."""
        if self._last_failure_time is None:
            return False
        return (self.clock() - self._last_failure_time) >= self.recovery_timeout_seconds


@dataclass
class AsyncCircuitBreaker:
    """Async sibling of CircuitBreaker — same state semantics, asyncio.Lock.

    Independent from the sync variant: state is not shared. Choose one
    per downstream. The probe-serialization, monotonic clock, and
    clock-injection contracts match the sync class exactly so behavior
    is portable between sync and async code paths.
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    half_open_max_probes: int = 1
    clock: Callable[[], float] = field(default=time.monotonic)

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float | None = field(default=None, init=False, repr=False)
    _probe_in_flight: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def call(
        self,
        coro_factory: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute coro with breaker protection. Raises CircuitOpenError if open."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    self._probe_in_flight = 1
                else:
                    raise CircuitOpenError(self.name)
            elif self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight >= self.half_open_max_probes:
                    raise CircuitOpenError(self.name)
                self._probe_in_flight += 1

        try:
            result = await coro_factory(*args, **kwargs)
        except Exception:
            await self.record_failure()
            raise
        await self.record_success()
        return result

    async def record_success(self) -> None:
        """Record a successful execution. Closes the circuit if HALF_OPEN."""
        async with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._probe_in_flight = 0

    async def record_failure(self) -> None:
        """Record a failed execution. Opens the circuit at threshold."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = self.clock()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._probe_in_flight = 0
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN

    async def state(self) -> CircuitState:
        """Get current circuit state."""
        async with self._lock:
            if self._state == CircuitState.OPEN and self._should_attempt_reset():
                return CircuitState.HALF_OPEN
            return self._state

    async def is_open(self) -> bool:
        """Return True if circuit is open (blocking requests)."""
        return (await self.state()) == CircuitState.OPEN

    async def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
            self._probe_in_flight = 0

    def _should_attempt_reset(self) -> bool:
        """Check if recovery timeout has elapsed since last failure."""
        if self._last_failure_time is None:
            return False
        return (self.clock() - self._last_failure_time) >= self.recovery_timeout_seconds
