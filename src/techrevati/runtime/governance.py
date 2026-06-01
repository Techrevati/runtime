"""
Governance plane — hard-stop limits enforced outside agent code.

The governance plane is the runtime's last line of defense against
runaway agent loops, budget overruns, persistent failures, and unbounded
tool use. Unlike ``UsageLimits`` (which the agent can in principle
catch + recover from), a ``GovernanceBreachError`` is *terminal*: it
propagates through the orchestrator without entering the recovery loop
or the failure classifier.

This is the Waxell pattern: enforcement at the governance plane, not
inside agent logic, so the agent cannot bypass its own limits. For EU
AI Act Article 14 (human oversight) and Article 15 (robustness)
deployments this is the technical primitive auditors expect to see.

Each limit is a small frozen dataclass with three pieces of data:

- ``value`` — the ceiling.
- ``scope`` — ``"session"``. Cross-session ``"thread"`` / ``"project"``
  scopes are intentionally rejected until they are implemented end-to-end.
- ``on_breach`` — ``"terminate"`` raises ``GovernanceBreachError``;
  ``"alert"`` emits a ``governance.alert`` event and continues. Use
  ``"alert"`` during rollout to measure breach rates before flipping to
  ``"terminate"`` in production.

Composition into a session:

>>> from techrevati.runtime import AgentSession
>>> from techrevati.runtime.governance import (
...     GovernancePlane, MaxIterationsLimit, MaxBudgetLimit,
... )
>>> plane = GovernancePlane(
...     limits=(
...         MaxIterationsLimit(value=10),
...         MaxBudgetLimit(value=5.00, on_breach="alert"),
...     ),
... )
>>> session = AgentSession(role="writer", phase="draft", governance=plane)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, cast

LimitScope = Literal["session"]
BreachAction = Literal["terminate", "alert"]
_VALID_LIMIT_SCOPES = frozenset(("session",))
_VALID_BREACH_ACTIONS = frozenset(("terminate", "alert"))


def _validate_non_empty_str(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _validate_finite_amount(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    if amount < 0:
        raise ValueError(f"{name} must be >= 0")
    return amount


def _validate_non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _validate_scope(scope: str) -> LimitScope:
    scope = _validate_non_empty_str("scope", scope)
    if scope not in _VALID_LIMIT_SCOPES:
        raise ValueError(
            "scope must be 'session'; thread/project scopes are not supported yet"
        )
    return cast(LimitScope, scope)


def _validate_breach_action(action: str) -> BreachAction:
    action = _validate_non_empty_str("on_breach", action)
    if action not in _VALID_BREACH_ACTIONS:
        raise ValueError("on_breach must be one of: terminate, alert")
    return cast(BreachAction, action)


class GovernanceBreachError(Exception):
    """Raised when a governance limit is exceeded with ``on_breach="terminate"``.

    Terminal by contract: the orchestrator re-raises this without
    invoking ``classify_exception`` or ``attempt_recovery``. Caller code
    that wraps a session in ``except Exception`` will still see it; if
    that's a problem in your code path, catch ``GovernanceBreachError``
    explicitly before the broader handler.
    """

    def __init__(
        self,
        *,
        limit_name: str,
        observed: float,
        ceiling: float,
        scope: LimitScope,
    ) -> None:
        limit_name = _validate_non_empty_str("limit_name", limit_name)
        observed = _validate_finite_amount("observed", observed)
        ceiling = _validate_finite_amount("ceiling", ceiling)
        scope = _validate_scope(scope)
        if observed <= ceiling:
            raise ValueError("observed must exceed ceiling for a governance breach")
        self.limit_name = limit_name
        self.observed = observed
        self.ceiling = ceiling
        self.scope = scope
        super().__init__(
            f"governance limit '{limit_name}' breached at {scope} scope: "
            f"observed {observed} > ceiling {ceiling}"
        )


@dataclass
class GovernanceState:
    """Mutable accumulator the plane reads on each evaluation.

    The orchestrator updates these counters as the session progresses;
    the plane never mutates them. Keep additions on this state cheap —
    it is read on every turn and every tool call.
    """

    turns: int = 0
    tool_calls: int = 0
    consecutive_failures: int = 0
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        self.turns = _validate_non_negative_int("turns", self.turns)
        self.tool_calls = _validate_non_negative_int("tool_calls", self.tool_calls)
        self.consecutive_failures = _validate_non_negative_int(
            "consecutive_failures", self.consecutive_failures
        )
        self.cost_usd = _validate_finite_amount("cost_usd", self.cost_usd)

    def record_turn_start(self) -> None:
        self.turns += 1

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_cost(self, cost_usd: float) -> None:
        self.cost_usd += _validate_finite_amount("cost_usd", cost_usd)


@dataclass(frozen=True)
class _LimitBase:
    """Shared fields. Concrete subclasses set ``name`` and pick a metric."""

    value: float
    scope: LimitScope = "session"
    on_breach: BreachAction = "terminate"

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_finite_amount("value", self.value))
        object.__setattr__(self, "scope", _validate_scope(self.scope))
        object.__setattr__(
            self,
            "on_breach",
            _validate_breach_action(self.on_breach),
        )
        object.__setattr__(
            self,
            "name",
            _validate_non_empty_str("name", getattr(self, "name", "")),
        )


@dataclass(frozen=True)
class MaxIterationsLimit(_LimitBase):
    """Cap total turns in the session. Pairs with ``AgentSession.max_iterations``.

    Difference from ``AgentSession.max_iterations``: the latter raises a
    domain ``MaxIterationsExceededError`` *which can* be caught and
    handled inside agent code. If it escapes the session context, the terminal
    failure class is still ``governance_breach``. ``MaxIterationsLimit`` with
    ``on_breach="terminate"`` raises ``GovernanceBreachError`` which is
    terminal and skips the recovery loop.
    """

    name: str = "max_iterations"


@dataclass(frozen=True)
class MaxBudgetLimit(_LimitBase):
    """Cap cumulative cost in USD. Pairs with ``UsageLimits.cost_usd_max``.

    Same difference as ``MaxIterationsLimit``: this is the hard-stop
    twin of the recoverable ``BudgetExceededError``.
    """

    name: str = "max_budget_usd"


@dataclass(frozen=True)
class MaxConsecutiveFailuresLimit(_LimitBase):
    """Cap consecutive recovery failures.

    Counts only consecutive failures — a single success resets the
    counter. Catches "agent retries the same broken thing forever"
    failure modes that ``RecoveryRecipe`` step retries alone do not.
    """

    name: str = "max_consecutive_failures"


@dataclass(frozen=True)
class MaxToolCallsLimit(_LimitBase):
    """Cap total tool calls in the session.

    Distinct from ``UsageLimits.tool_calls_max`` only in that this is a
    hard-stop. Choose this when "the agent is spamming tool calls" is a
    safety / cost concern, not a usage telemetry concern.
    """

    name: str = "max_tool_calls"


Limit = (
    MaxIterationsLimit
    | MaxBudgetLimit
    | MaxConsecutiveFailuresLimit
    | MaxToolCallsLimit
)


@dataclass(frozen=True)
class LimitOutcome:
    """Result of evaluating one limit against current state."""

    breached: bool
    limit_name: str
    observed: float
    ceiling: float
    scope: LimitScope
    on_breach: BreachAction

    def __post_init__(self) -> None:
        if not isinstance(self.breached, bool):
            raise TypeError("breached must be a bool")
        limit_name = _validate_non_empty_str("limit_name", self.limit_name)
        observed = _validate_finite_amount("observed", self.observed)
        ceiling = _validate_finite_amount("ceiling", self.ceiling)
        expected = observed > ceiling
        if self.breached != expected:
            raise ValueError("breached must match observed and ceiling")
        object.__setattr__(self, "limit_name", limit_name)
        object.__setattr__(self, "observed", observed)
        object.__setattr__(self, "ceiling", ceiling)
        object.__setattr__(self, "scope", _validate_scope(self.scope))
        object.__setattr__(
            self,
            "on_breach",
            _validate_breach_action(self.on_breach),
        )


def _evaluate_one(limit: Limit, state: GovernanceState) -> LimitOutcome:
    if isinstance(limit, MaxIterationsLimit):
        observed: float = float(state.turns)
    elif isinstance(limit, MaxBudgetLimit):
        observed = state.cost_usd
    elif isinstance(limit, MaxConsecutiveFailuresLimit):
        observed = float(state.consecutive_failures)
    else:  # MaxToolCallsLimit
        observed = float(state.tool_calls)
    return LimitOutcome(
        breached=observed > limit.value,
        limit_name=limit.name,
        observed=observed,
        ceiling=limit.value,
        scope=limit.scope,
        on_breach=limit.on_breach,
    )


@dataclass
class GovernancePlane:
    """Composes a set of limits and enforces them against a ``GovernanceState``.

    The orchestrator constructs and owns the state; the plane is a
    pure evaluator + raiser. This separation keeps the limit objects
    immutable and the per-session counter mutable, which is what tests
    and audit replays both want.
    """

    limits: tuple[Limit, ...]
    state: GovernanceState = field(default_factory=GovernanceState)

    def __post_init__(self) -> None:
        if not isinstance(self.state, GovernanceState):
            raise ValueError("state must be a GovernanceState")
        limits = tuple(self.limits)
        for limit in limits:
            if not isinstance(
                limit,
                (
                    MaxIterationsLimit,
                    MaxBudgetLimit,
                    MaxConsecutiveFailuresLimit,
                    MaxToolCallsLimit,
                ),
            ):
                raise ValueError("limits must contain governance limit objects")
        self.limits = limits

    def for_session(self) -> GovernancePlane:
        """Return the same limits with a fresh per-session state."""
        return GovernancePlane(limits=self.limits)

    def evaluate(self) -> list[LimitOutcome]:
        """Evaluate every limit. Returns outcomes (breached or not).

        Does NOT raise. Callers that want enforcement call
        ``enforce()`` instead; this method is exposed for inspection
        and for the ``"alert"`` code path which only emits events.
        """
        return [_evaluate_one(limit, self.state) for limit in self.limits]

    def enforce(self) -> list[LimitOutcome]:
        """Evaluate and raise on the first ``on_breach="terminate"`` breach.

        Returns the full list of outcomes (including breached ones with
        ``on_breach="alert"``) so the caller can record them via the
        event sink. Raises ``GovernanceBreachError`` on the first
        terminate-breach.
        """
        outcomes = self.evaluate()
        for outcome in outcomes:
            if outcome.breached and outcome.on_breach == "terminate":
                raise GovernanceBreachError(
                    limit_name=outcome.limit_name,
                    observed=outcome.observed,
                    ceiling=outcome.ceiling,
                    scope=outcome.scope,
                )
        return outcomes


__all__ = [
    "BreachAction",
    "GovernanceBreachError",
    "GovernancePlane",
    "GovernanceState",
    "Limit",
    "LimitOutcome",
    "LimitScope",
    "MaxBudgetLimit",
    "MaxConsecutiveFailuresLimit",
    "MaxIterationsLimit",
    "MaxToolCallsLimit",
]
