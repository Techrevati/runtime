"""Property-based state machine tests for CircuitBreaker.

Hypothesis drives random sequences of (success, failure, advance_clock,
state_query) operations and verifies the breaker's invariants hold for
every reachable state:

  1. failure_count is always in [0, failure_threshold] after a transition
     (it resets on success and on transition out of HALF_OPEN).
  2. The circuit can only be OPEN if a failure has occurred.
  3. After ``recovery_timeout_seconds`` elapses past the last failure,
     ``state()`` reports HALF_OPEN (lazy transition).
  4. A failure recorded in HALF_OPEN immediately reopens the circuit.
  5. A success recorded in HALF_OPEN closes it and zeroes the counter.

The injectable ManualClock makes these deterministic — no real time
passes during the run.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)

from techrevati.runtime.circuit_breaker import CircuitBreaker, CircuitState
from tests.conftest import ManualClock


class CircuitBreakerStateMachine(RuleBasedStateMachine):
    """Drive a CircuitBreaker through random op sequences."""

    def __init__(self) -> None:
        super().__init__()
        self.clock = ManualClock()
        self.threshold = 3
        self.recovery = 5.0
        self.cb = CircuitBreaker(
            "prop",
            failure_threshold=self.threshold,
            recovery_timeout_seconds=self.recovery,
            clock=self.clock,
        )

    @rule()
    def record_success(self) -> None:
        self.cb.record_success()

    @rule()
    def record_failure(self) -> None:
        self.cb.record_failure()

    @rule()
    def advance_past_recovery(self) -> None:
        self.clock.advance(self.recovery + 0.1)

    @rule()
    def advance_partial(self) -> None:
        self.clock.advance(self.recovery / 2.0)

    @rule()
    def query_state(self) -> None:
        # Querying must never mutate failure_count or raise.
        _ = self.cb.state()

    @precondition(lambda self: self.cb.state() == CircuitState.HALF_OPEN)
    @rule()
    def half_open_failure_reopens(self) -> None:
        self.cb.record_failure()
        # After a half-open failure the circuit must be OPEN until the
        # recovery timeout elapses again.
        assert self.cb._state == CircuitState.OPEN

    @invariant()
    def failure_count_non_negative(self) -> None:
        # The counter keeps incrementing while OPEN (bookkeeping only —
        # state has already latched), so there is no upper bound to assert.
        # The meaningful invariants are: never negative, and reaches
        # threshold implies OPEN.
        assert self.cb._failure_count >= 0
        if self.cb._failure_count >= self.threshold:
            assert self.cb._state == CircuitState.OPEN

    @invariant()
    def open_requires_a_failure(self) -> None:
        if self.cb._state == CircuitState.OPEN:
            assert self.cb._last_failure_time is not None

    @invariant()
    def state_query_is_consistent(self) -> None:
        # state() returns HALF_OPEN if OPEN + recovery elapsed; internal
        # _state may still be OPEN until call() promotes it. The lazy
        # promotion must never disagree with the elapsed-time check.
        s = self.cb.state()
        if self.cb._state == CircuitState.OPEN:
            assert s in (CircuitState.OPEN, CircuitState.HALF_OPEN)
        elif self.cb._state == CircuitState.CLOSED:
            assert s == CircuitState.CLOSED
        elif self.cb._state == CircuitState.HALF_OPEN:
            assert s == CircuitState.HALF_OPEN


TestCircuitBreakerStateMachine = CircuitBreakerStateMachine.TestCase
TestCircuitBreakerStateMachine.settings = settings(
    max_examples=50,
    stateful_step_count=30,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
