"""
Circuit Breaker — Fault-tolerant execution with state machine.

Implements the Circuit Breaker pattern to prevent cascading failures
when calling unreliable services or operations. Transitions between
CLOSED (normal), OPEN (failing), and HALF_OPEN (testing) states.

Thread-safe with configurable failure threshold, recovery timeout,
and number of in-flight probes permitted in HALF_OPEN. Uses
``time.monotonic`` for duration checks so clock jumps don't affect
behavior; the clock function is injectable for deterministic tests.

Probe bookkeeping in HALF_OPEN:

- At most ``half_open_max_probes`` probes are issued per cycle, not merely
  concurrently, so a cycle always finishes under sustained load: the circuit
  closes once every admitted probe has succeeded.
- A cycle whose probes have not reported after ``recovery_timeout_seconds`` is
  abandoned and a new probe is admitted, so a hung probe (for example a worker
  thread orphaned by ``run_turn(timeout=...)``) cannot block recovery forever.
  With ``recovery_timeout_seconds=0`` there is no such window and no reclaim.
- Each probe belongs to a generation. A result from an abandoned or failed cycle
  is ignored; it says nothing about the current one.
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

    def __reduce__(self) -> tuple[type[CircuitOpenError], tuple[str]]:
        return (type(self), (self.name,))


class _BreakerStateMachine:
    """State transitions shared by both breakers; callers hold the lock."""

    name: str
    failure_threshold: int
    recovery_timeout_seconds: float
    half_open_max_probes: int
    clock: Callable[[], float]
    _state: CircuitState
    _failure_count: int
    _last_failure_time: float | None
    _probe_in_flight: int
    _probes_issued: int
    _half_open_since: float | None
    _generation: int

    def _validate_config(self) -> None:
        if self.failure_threshold <= 0:
            raise ValueError("failure_threshold must be > 0")
        # Written so NaN fails too: it compares false with everything, which
        # would otherwise leave the circuit unable to ever leave OPEN.
        if not self.recovery_timeout_seconds >= 0:
            raise ValueError("recovery_timeout_seconds must be >= 0")
        if self.half_open_max_probes <= 0:
            raise ValueError("half_open_max_probes must be > 0")

    def _should_attempt_reset(self) -> bool:
        """Check if recovery timeout has elapsed since last failure."""
        if self._last_failure_time is None:
            return False
        return (self.clock() - self._last_failure_time) >= self.recovery_timeout_seconds

    def _start_probe_cycle_locked(self) -> int:
        self._state = CircuitState.HALF_OPEN
        self._probe_in_flight = 1
        self._probes_issued = 1
        self._half_open_since = self.clock()
        return self._generation

    def _probe_cycle_expired_locked(self) -> bool:
        if self._half_open_since is None or self.recovery_timeout_seconds <= 0:
            return False
        elapsed = self.clock() - self._half_open_since
        return elapsed >= self.recovery_timeout_seconds

    def _admit_locked(self) -> int | None:
        """Admit a call. Returns the probe's generation, or None if not a probe."""
        if self._state == CircuitState.OPEN:
            if not self._should_attempt_reset():
                raise CircuitOpenError(self.name)
            return self._start_probe_cycle_locked()
        if self._state == CircuitState.HALF_OPEN:
            if self._probes_issued < self.half_open_max_probes:
                self._probe_in_flight += 1
                self._probes_issued += 1
                return self._generation
            if not self._probe_cycle_expired_locked():
                raise CircuitOpenError(self.name)
            self._generation += 1
            return self._start_probe_cycle_locked()
        return None

    def _is_stale_locked(self, generation: int | None) -> bool:
        return generation is not None and generation != self._generation

    def _open_locked(self) -> None:
        self._state = CircuitState.OPEN
        self._probe_in_flight = 0
        self._probes_issued = 0
        self._half_open_since = None
        self._generation += 1

    def _record_failure_locked(self) -> None:
        self._failure_count += 1
        self._last_failure_time = self.clock()
        if self._state == CircuitState.HALF_OPEN or (
            self._state == CircuitState.CLOSED
            and self._failure_count >= self.failure_threshold
        ):
            # A failed probe also drops every in-flight permit of its cycle.
            self._open_locked()

    def _record_success_locked(self) -> None:
        self._failure_count = 0
        # OPEN with an elapsed timeout is what ``state()`` reports as HALF_OPEN.
        if self._state == CircuitState.HALF_OPEN or (
            self._state == CircuitState.OPEN and self._should_attempt_reset()
        ):
            self._complete_probe_locked()

    def _record_call_success_locked(self, generation: int | None) -> None:
        if self._is_stale_locked(generation):
            return
        if self._state == CircuitState.HALF_OPEN and generation is not None:
            self._complete_probe_locked()
            return
        if self._state == CircuitState.CLOSED:
            self._failure_count = 0
            self._last_failure_time = None

    def _complete_probe_locked(self) -> None:
        if self._probe_in_flight > 0:
            self._probe_in_flight -= 1
        if self._probe_in_flight == 0:
            self._reset_locked()

    def _release_probe_locked(self, generation: int) -> None:
        if self._is_stale_locked(generation):
            return
        if self._state == CircuitState.HALF_OPEN and self._probe_in_flight > 0:
            self._probe_in_flight -= 1
            self._probes_issued -= 1

    def _state_locked(self) -> CircuitState:
        if self._state == CircuitState.OPEN and self._should_attempt_reset():
            return CircuitState.HALF_OPEN
        return self._state

    def _reset_locked(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = None
        self._probe_in_flight = 0
        self._probes_issued = 0
        self._half_open_since = None


@dataclass
class CircuitBreaker(_BreakerStateMachine):
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
        Probe calls issued per HALF_OPEN cycle. Default 1 (Polly
        convention); raising to N spreads recovery risk over multiple
        calls (Resilience4j defaults to 10). The circuit closes once every
        admitted probe has succeeded.
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
    _probes_issued: int = field(default=0, init=False, repr=False)
    _half_open_since: float | None = field(default=None, init=False, repr=False)
    _generation: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._validate_config()

    def call(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute fn with breaker protection. Raises CircuitOpenError if open.

        In HALF_OPEN state, at most ``half_open_max_probes`` calls are admitted
        per cycle; excess callers receive ``CircuitOpenError`` until the cycle
        completes or expires.
        """
        with self._lock:
            generation = self._admit_locked()

        try:
            result = fn(*args, **kwargs)
        except Exception:
            self._record_call_failure(generation)
            raise
        except BaseException:
            if generation is not None:
                with self._lock:
                    self._release_probe_locked(generation)
            raise
        with self._lock:
            self._record_call_success_locked(generation)
        return result

    def _record_call_failure(self, generation: int | None) -> None:
        with self._lock:
            stale = self._is_stale_locked(generation)
        if not stale:
            self.record_failure()

    def record_success(self) -> None:
        """Record a successful execution. Closes the circuit if HALF_OPEN."""
        with self._lock:
            self._record_success_locked()

    def record_failure(self) -> None:
        """Record a failed execution. Opens the circuit at threshold."""
        with self._lock:
            self._record_failure_locked()

    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state_locked()

    def is_open(self) -> bool:
        """Return True if circuit is open (blocking requests)."""
        return self.state() == CircuitState.OPEN

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        with self._lock:
            self._reset_locked()
            self._generation += 1


@dataclass
class AsyncCircuitBreaker(_BreakerStateMachine):
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
    _probes_issued: int = field(default=0, init=False, repr=False)
    _half_open_since: float | None = field(default=None, init=False, repr=False)
    _generation: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_config()

    async def call(
        self,
        coro_factory: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute coro with breaker protection. Raises CircuitOpenError if open."""
        async with self._lock:
            generation = self._admit_locked()

        try:
            result = await coro_factory(*args, **kwargs)
        except Exception:
            await self._record_call_failure(generation)
            raise
        except BaseException:
            if generation is not None:
                async with self._lock:
                    self._release_probe_locked(generation)
            raise
        async with self._lock:
            self._record_call_success_locked(generation)
        return result

    async def _record_call_failure(self, generation: int | None) -> None:
        async with self._lock:
            stale = self._is_stale_locked(generation)
        if not stale:
            await self.record_failure()

    async def record_success(self) -> None:
        """Record a successful execution. Closes the circuit if HALF_OPEN."""
        async with self._lock:
            self._record_success_locked()

    async def record_failure(self) -> None:
        """Record a failed execution. Opens the circuit at threshold."""
        async with self._lock:
            self._record_failure_locked()

    async def state(self) -> CircuitState:
        """Get current circuit state."""
        async with self._lock:
            return self._state_locked()

    async def is_open(self) -> bool:
        """Return True if circuit is open (blocking requests)."""
        return (await self.state()) == CircuitState.OPEN

    async def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        async with self._lock:
            self._reset_locked()
            self._generation += 1
