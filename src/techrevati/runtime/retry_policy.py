"""
Retry Policy — Failure classification and recipe lookup.

Maps failure scenarios to structured recovery steps with bounded
attempts and an escalation policy. The caller decides whether and
how to retry; this module provides the recipe + bookkeeping.

classify_exception() bridges Python exceptions to failure scenarios.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from techrevati.runtime._internal import (
    _validate_finite_number,
    _validate_non_empty_str,
    _validate_non_negative_int,
    _validate_positive_int,
)

logger = logging.getLogger("techrevati.runtime.retry")
logger.addHandler(logging.NullHandler())


class FailureScenario(str, Enum):
    """Failure types that can be automatically recovered."""

    LLM_TIMEOUT = "llm_timeout"
    LLM_ERROR = "llm_error"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    CONTEXT_OVERFLOW = "context_overflow"
    DEPENDENCY_TIMEOUT = "dependency_timeout"
    MEMORY_CORRUPTION = "memory_corruption"
    PROVIDER_FAILURE = "provider_failure"


class RecoveryStep(str, Enum):
    """Actions that can be taken to recover from failure."""

    RETRY_WITH_BACKOFF = "retry_with_backoff"
    RETRY_WITH_SMALLER_CONTEXT = "retry_with_smaller_context"
    SWITCH_PROVIDER = "switch_provider"
    RESTART_AGENT = "restart_agent"
    CLEAR_MEMORY_CACHE = "clear_memory_cache"
    REDUCE_TOOL_SET = "reduce_tool_set"
    ESCALATE_TO_HUMAN = "escalate_to_human"


class EscalationPolicy(str, Enum):
    """What to do when max recovery attempts are exhausted."""

    ALERT_HUMAN = "alert_human"
    LOG_AND_CONTINUE = "log_and_continue"
    ABORT = "abort"


_RECOVERY_OUTCOMES: frozenset[str] = frozenset(
    {"recovered", "partial_recovery", "escalation_required"}
)
_RECOVERY_EVENT_TYPES: frozenset[str] = frozenset(
    {"attempted", "succeeded", "failed", "escalated"}
)


def _coerce_scenario(value: FailureScenario | str) -> FailureScenario:
    if isinstance(value, FailureScenario):
        return value
    if isinstance(value, str):
        try:
            return FailureScenario(value)
        except ValueError as exc:
            raise ValueError("scenario must be a valid FailureScenario") from exc
    raise TypeError("scenario must be a FailureScenario")


def _coerce_step(field_name: str, value: RecoveryStep | str) -> RecoveryStep:
    if isinstance(value, RecoveryStep):
        return value
    if isinstance(value, str):
        try:
            return RecoveryStep(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid RecoveryStep") from exc
    raise TypeError(f"{field_name} must be a RecoveryStep")


def _coerce_escalation_policy(
    value: EscalationPolicy | str,
) -> EscalationPolicy:
    if isinstance(value, EscalationPolicy):
        return value
    if isinstance(value, str):
        try:
            return EscalationPolicy(value)
        except ValueError as exc:
            raise ValueError(
                "escalation_policy must be a valid EscalationPolicy"
            ) from exc
    raise TypeError("escalation_policy must be an EscalationPolicy")


def _validate_optional_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _validate_positive_number(field_name: str, value: float) -> float:
    number = _validate_finite_number(field_name, value)
    if number <= 0:
        raise ValueError(f"{field_name} must be positive")
    return number


def _validate_non_negative_number(field_name: str, value: float) -> float:
    number = _validate_finite_number(field_name, value)
    if number < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return number


def _normalize_steps(steps: Any) -> tuple[RecoveryStep, ...]:
    if isinstance(steps, (str, bytes)):
        raise TypeError("steps must be a sequence of RecoveryStep values")
    try:
        normalized = tuple(_coerce_step("steps", step) for step in steps)
    except TypeError as exc:
        raise TypeError("steps must be a sequence of RecoveryStep values") from exc
    if not normalized:
        raise ValueError("steps must not be empty")
    return normalized


def _normalize_step_retries(
    retries: dict[RecoveryStep, int],
    steps: tuple[RecoveryStep, ...],
) -> dict[RecoveryStep, int]:
    if not isinstance(retries, dict):
        raise TypeError("step_retries must be a dict")
    normalized: dict[RecoveryStep, int] = {}
    valid_steps = set(steps)
    for raw_step, raw_budget in retries.items():
        step = _coerce_step("step_retries", raw_step)
        if step not in valid_steps:
            raise ValueError("step_retries contains a step not present in steps")
        normalized[step] = _validate_non_negative_int("step_retries budget", raw_budget)
    return normalized


def _normalize_string_list(field_name: str, values: list[str]) -> list[str]:
    if not isinstance(values, list):
        raise TypeError(f"{field_name} must be a list")
    return [_validate_non_empty_str(f"{field_name} item", value) for value in values]


def _copy_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise TypeError("result must be a dict")
    copied = deepcopy(result)
    for key in copied:
        if not isinstance(key, str):
            raise TypeError("result keys must be strings")
    return copied


def _copy_event(event: RecoveryEvent) -> RecoveryEvent:
    if not isinstance(event, RecoveryEvent):
        raise TypeError("event must be a RecoveryEvent")
    return RecoveryEvent(
        event_type=event.event_type,
        scenario=event.scenario,
        recipe_steps=list(event.recipe_steps),
        result=event.result,
        timestamp=event.timestamp,
    )


@dataclass(frozen=True)
class RecoveryRecipe:
    """Recovery plan for a failure scenario.

    ``step_retries`` is an optional per-step retry budget. When a step
    fails (the recovery context's ``_fail_at_attempt`` hook returns
    True), the executor retries that same step up to ``step_retries[step]``
    times before declaring it a failure and moving on to the next step
    (which becomes ``remaining_steps`` in partial recovery). Missing keys
    default to a budget of 1 (single attempt) — preserving 0.1.0 / 0.2.0
    semantics.

    Example::

        RecoveryRecipe(
            scenario=FailureScenario.LLM_ERROR,
            steps=(RecoveryStep.RETRY_WITH_BACKOFF, RecoveryStep.SWITCH_PROVIDER),
            max_attempts=2,
            escalation_policy=EscalationPolicy.ALERT_HUMAN,
            step_retries={RecoveryStep.RETRY_WITH_BACKOFF: 3},
        )

    fires the backoff step up to three times before failing over to the
    provider switch.
    """

    scenario: FailureScenario
    steps: tuple[RecoveryStep, ...]
    max_attempts: int
    escalation_policy: EscalationPolicy
    step_retries: dict[RecoveryStep, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        scenario = _coerce_scenario(self.scenario)
        steps = _normalize_steps(self.steps)
        object.__setattr__(self, "scenario", scenario)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(
            self,
            "max_attempts",
            _validate_positive_int("max_attempts", self.max_attempts),
        )
        object.__setattr__(
            self,
            "escalation_policy",
            _coerce_escalation_policy(self.escalation_policy),
        )
        object.__setattr__(
            self, "step_retries", _normalize_step_retries(self.step_retries, steps)
        )


@dataclass
class RecoveryResult:
    """Outcome of a recovery attempt."""

    outcome: Literal["recovered", "partial_recovery", "escalation_required"]
    steps_taken: int = 0
    recovered_steps: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, str) or self.outcome not in _RECOVERY_OUTCOMES:
            raise ValueError("outcome must be a valid recovery outcome")
        self.steps_taken = _validate_non_negative_int("steps_taken", self.steps_taken)
        self.recovered_steps = _normalize_string_list(
            "recovered_steps", self.recovered_steps
        )
        self.remaining_steps = _normalize_string_list(
            "remaining_steps", self.remaining_steps
        )
        self.reason = _validate_optional_str("reason", self.reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "steps_taken": self.steps_taken,
            "recovered_steps": list(self.recovered_steps),
            "remaining_steps": list(self.remaining_steps),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RecoveryEvent:
    """Structured record of a recovery action."""

    event_type: str  # attempted, succeeded, failed, escalated
    scenario: str
    recipe_steps: list[str]
    result: dict[str, Any]
    timestamp: str

    def __post_init__(self) -> None:
        event_type = _validate_non_empty_str("event_type", self.event_type)
        if event_type not in _RECOVERY_EVENT_TYPES:
            raise ValueError("event_type must be a valid recovery event type")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "scenario", _coerce_scenario(self.scenario).value)
        object.__setattr__(
            self,
            "recipe_steps",
            _normalize_string_list("recipe_steps", self.recipe_steps),
        )
        object.__setattr__(self, "result", _copy_result(self.result))
        object.__setattr__(
            self, "timestamp", _validate_non_empty_str("timestamp", self.timestamp)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "scenario": self.scenario,
            "recipe_steps": list(self.recipe_steps),
            "result": deepcopy(self.result),
            "timestamp": self.timestamp,
        }


# -- Recipe registry --

_RECIPES: dict[FailureScenario, RecoveryRecipe] = {
    FailureScenario.LLM_TIMEOUT: RecoveryRecipe(
        scenario=FailureScenario.LLM_TIMEOUT,
        steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
        max_attempts=2,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.LLM_ERROR: RecoveryRecipe(
        scenario=FailureScenario.LLM_ERROR,
        steps=(RecoveryStep.RETRY_WITH_BACKOFF, RecoveryStep.SWITCH_PROVIDER),
        max_attempts=2,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.TOOL_EXECUTION_ERROR: RecoveryRecipe(
        scenario=FailureScenario.TOOL_EXECUTION_ERROR,
        steps=(RecoveryStep.RESTART_AGENT,),
        max_attempts=1,
        escalation_policy=EscalationPolicy.LOG_AND_CONTINUE,
    ),
    FailureScenario.CONTEXT_OVERFLOW: RecoveryRecipe(
        scenario=FailureScenario.CONTEXT_OVERFLOW,
        steps=(RecoveryStep.RETRY_WITH_SMALLER_CONTEXT, RecoveryStep.REDUCE_TOOL_SET),
        max_attempts=1,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.DEPENDENCY_TIMEOUT: RecoveryRecipe(
        scenario=FailureScenario.DEPENDENCY_TIMEOUT,
        steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
        max_attempts=1,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.MEMORY_CORRUPTION: RecoveryRecipe(
        scenario=FailureScenario.MEMORY_CORRUPTION,
        steps=(RecoveryStep.CLEAR_MEMORY_CACHE, RecoveryStep.RESTART_AGENT),
        max_attempts=1,
        escalation_policy=EscalationPolicy.ABORT,
    ),
    FailureScenario.PROVIDER_FAILURE: RecoveryRecipe(
        scenario=FailureScenario.PROVIDER_FAILURE,
        steps=(RecoveryStep.SWITCH_PROVIDER, RecoveryStep.RETRY_WITH_BACKOFF),
        max_attempts=2,
        escalation_policy=EscalationPolicy.ABORT,
    ),
}


def recipe_for(scenario: FailureScenario | str) -> RecoveryRecipe:
    """Look up the recovery recipe for a failure scenario."""
    scenario = _coerce_scenario(scenario)
    return _RECIPES[scenario]


class RecoveryContext:
    """Tracks recovery attempts per scenario within a session."""

    def __init__(self) -> None:
        self._attempts: dict[FailureScenario, int] = {}
        self._events: list[RecoveryEvent] = []

    def attempt_count(self, scenario: FailureScenario | str) -> int:
        scenario = _coerce_scenario(scenario)
        return self._attempts.get(scenario, 0)

    @property
    def events(self) -> list[RecoveryEvent]:
        return [_copy_event(event) for event in self._events]

    def _fail_at_step(self) -> int | None:
        """Override in test subclasses to simulate partial recovery.

        Returns the index of the first step that should fail (all
        subsequent steps also fail), or None if every step succeeds.
        """
        return None

    def _fail_at_attempt(
        self, step: RecoveryStep, step_index: int, attempt: int
    ) -> bool:
        """Return True if this specific (step, attempt) should fail.

        Default semantics layered on top of ``_fail_at_step`` so the
        legacy hook keeps working: every attempt of a "failing" step
        fails. Override in test subclasses to simulate "fails on
        attempt 0, succeeds on attempt 1" for ``step_retries`` testing.
        """
        del step, attempt  # silence unused-argument lint in default impl
        fail_at = self._fail_at_step()
        return fail_at is not None and step_index >= fail_at

    def _emit(
        self,
        event_type: str,
        scenario: FailureScenario,
        recipe: RecoveryRecipe,
        result: RecoveryResult,
    ) -> None:
        self._events.append(
            RecoveryEvent(
                event_type=event_type,
                scenario=scenario.value,
                recipe_steps=[s.value for s in recipe.steps],
                result=result.to_dict(),
                timestamp=datetime.now(UTC).isoformat(),
            )
        )


def attempt_recovery(
    scenario: FailureScenario | str,
    ctx: RecoveryContext,
) -> RecoveryResult:
    """Attempt recovery for a failure scenario.

    Returns RecoveryResult with outcome: recovered, partial_recovery,
    or escalation_required.
    """
    if not isinstance(ctx, RecoveryContext):
        raise TypeError("ctx must be a RecoveryContext")
    scenario = _coerce_scenario(scenario)
    recipe = recipe_for(scenario)

    # Check max attempts
    current = ctx.attempt_count(scenario)
    if current >= recipe.max_attempts:
        result = RecoveryResult(
            outcome="escalation_required",
            reason=(
                f"max attempts ({recipe.max_attempts}) exhausted for {scenario.value}"
            ),
        )
        ctx._emit("escalated", scenario, recipe, result)
        return result

    # Increment attempt counter (even before escalation on future calls)
    ctx._attempts[scenario] = current + 1

    # Execute steps, honoring per-step retry budgets.
    recovered_steps: list[str] = []
    remaining_steps: list[str] = []

    for i, step in enumerate(recipe.steps):
        budget = recipe.step_retries.get(step, 1)
        step_succeeded = False
        for step_attempt in range(budget):
            if ctx._fail_at_attempt(step, i, step_attempt):
                continue
            step_succeeded = True
            break

        if not step_succeeded:
            remaining_steps = [s.value for s in recipe.steps[i:]]
            result = RecoveryResult(
                outcome="partial_recovery",
                steps_taken=len(recovered_steps),
                recovered_steps=recovered_steps,
                remaining_steps=remaining_steps,
            )
            ctx._emit("failed", scenario, recipe, result)
            return result
        recovered_steps.append(step.value)

    result = RecoveryResult(
        outcome="recovered",
        steps_taken=len(recovered_steps),
        recovered_steps=recovered_steps,
    )
    ctx._emit("succeeded", scenario, recipe, result)
    return result


async def aattempt_recovery(
    scenario: FailureScenario | str,
    ctx: RecoveryContext,
    *,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> RecoveryResult:
    """Async variant of attempt_recovery.

    Behavior matches the sync version step-for-step. The ``sleeper``
    parameter is reserved for future steps that need to await a delay
    (e.g. backoff). Pass ``asyncio.sleep`` in production code; pass a
    no-op or a fake in tests for determinism. Today no step in
    ``RecoveryRecipe`` actually sleeps, so ``sleeper`` is unused in
    practice — but the contract is established now so 0.1.0 callers
    can rely on it.
    """
    if sleeper is None:
        sleeper = asyncio.sleep
    elif not callable(sleeper):
        raise TypeError("sleeper must be callable")
    # Sleeper is reserved for future use; record it on the context so
    # subclasses can read it. The default behavior is identical to sync.
    _ = sleeper  # noqa: F841 - reserved for future steps
    return attempt_recovery(scenario, ctx)


# -- Step handlers (actual implementations) --


JitterMode = Literal["none", "full", "equal", "decorrelated"]


def backoff_delay(
    attempt: int,
    base: float = 2.0,
    jitter: bool | JitterMode = "decorrelated",
    cap: float = 60.0,
    prev_delay: float = 0.0,
) -> float:
    """Calculate backoff delay in seconds with selectable jitter algorithm.

    Algorithms follow Marc Brooker / AWS Architecture Blog
    (https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/):

    - ``"none"`` — pure exponential ``base ** attempt`` (capped).
    - ``"full"`` — ``uniform(0, cap_exp)``: maximum spread, lowest contention.
    - ``"equal"`` — ``cap_exp/2 + uniform(0, cap_exp/2)``: half deterministic.
    - ``"decorrelated"`` (default) — ``uniform(base, prev_delay * 3)``: AWS's
      fastest algorithm. Callers passing 0 for ``prev_delay`` get ``base``.

    Backwards compatibility: ``jitter=True`` (bool) maps to ``"full"`` and
    ``jitter=False`` maps to ``"none"``. The ``base ** attempt + 25% noise``
    formula from 0.0.0 is gone — use ``"equal"`` for similar behavior.
    """
    attempt = _validate_non_negative_int("attempt", attempt)
    base = _validate_positive_number("base", base)
    cap = _validate_positive_number("cap", cap)
    prev_delay = _validate_non_negative_number("prev_delay", prev_delay)
    if jitter is True:
        mode: JitterMode = "full"
    elif jitter is False:
        mode = "none"
    else:
        mode = jitter

    try:
        cap_exp = min(cap, base**attempt)
    except OverflowError:
        cap_exp = cap

    if mode == "none":
        return cap_exp
    if mode == "full":
        return random.uniform(0.0, cap_exp)
    if mode == "equal":
        half = cap_exp / 2.0
        return half + random.uniform(0.0, half)
    if mode == "decorrelated":
        anchor = prev_delay if prev_delay > 0 else base
        # Clamp the upper bound to at least ``base`` so a tiny ``prev_delay``
        # (where ``anchor * 3 < base``) cannot invert the range and yield a
        # delay below ``base``, which the documented lower bound forbids.
        return min(cap, random.uniform(base, max(base, anchor * 3.0)))
    raise ValueError(f"unknown jitter mode: {mode!r}")


def next_provider(
    available_providers: list[str],
    current_provider: str,
) -> str | None:
    """Select the next fallback provider, skipping the current one."""
    if isinstance(available_providers, (str, bytes)):
        raise TypeError("available_providers must be a sequence of provider names")
    current_provider = _validate_non_empty_str("current_provider", current_provider)
    candidates = []
    for provider in available_providers:
        provider = _validate_non_empty_str("available_providers item", provider)
        if provider != current_provider:
            candidates.append(provider)
    return candidates[0] if candidates else None


def smaller_context_budget(current_chars: int, reduction: float = 0.75) -> int:
    """Calculate a reduced context budget (75% of current by default)."""
    current_chars = _validate_non_negative_int("current_chars", current_chars)
    reduction = _validate_finite_number("reduction", reduction)
    if not 0.0 <= reduction <= 1.0:
        raise ValueError("reduction must be between 0 and 1")
    return int(current_chars * reduction)


# -- Exception classifier --


# Type-based dispatch first — subclass entries MUST come before their bases
# (e.g. ConnectionRefusedError before ConnectionError) so the tuple-order
# scan gives the most specific match.
_EXCEPTION_TYPE_MAPPING: tuple[tuple[type[BaseException], FailureScenario], ...] = (
    (asyncio.TimeoutError, FailureScenario.LLM_TIMEOUT),
    (TimeoutError, FailureScenario.LLM_TIMEOUT),
    (ConnectionRefusedError, FailureScenario.PROVIDER_FAILURE),
    (ConnectionResetError, FailureScenario.PROVIDER_FAILURE),
    (ConnectionError, FailureScenario.PROVIDER_FAILURE),
    (json.JSONDecodeError, FailureScenario.MEMORY_CORRUPTION),
    (OSError, FailureScenario.DEPENDENCY_TIMEOUT),
)


def _classify_by_type(error: BaseException) -> FailureScenario | None:
    """Type-based dispatch against the stdlib exception mapping."""
    for exc_type, scenario in _EXCEPTION_TYPE_MAPPING:
        if isinstance(error, exc_type):
            return scenario
    return None


def classify_exception(error: Exception) -> FailureScenario:
    """Map a Python exception to a FailureScenario for recovery.

    Two-pass dispatch:

    1. **Type-based** — ``isinstance`` against well-known stdlib classes
       (``TimeoutError``, ``ConnectionError`` family, ``JSONDecodeError``,
       ``OSError``). Walks the exception chain via ``__cause__`` /
       ``__context__`` so a ``RuntimeError`` wrapping a ``ConnectionError`` is
       still classified as ``PROVIDER_FAILURE`` and a wrapped disk or file
       system error is still classified as ``DEPENDENCY_TIMEOUT``.
    2. **String match** — provider SDKs that don't expose stdlib types
       fall through to substring matching on the rendered message.
    """
    # Pass 1: type-based, with __cause__ / __context__ walk. Mirrors the
    # stdlib traceback rule: prefer explicit cause; fall back to implicit
    # context only when ``__suppress_context__`` is not set (i.e. caller
    # did NOT use ``raise X from None``).
    seen: set[int] = set()
    cursor: BaseException | None = error
    while cursor is not None and id(cursor) not in seen:
        seen.add(id(cursor))
        scenario = _classify_by_type(cursor)
        if scenario is not None:
            return scenario
        nxt: BaseException | None = cursor.__cause__
        if nxt is None and not cursor.__suppress_context__:
            nxt = cursor.__context__
        cursor = nxt

    # Pass 2: string substring match against the rendered message.
    error_str = str(error).lower()

    if "timeout" in error_str or "timed out" in error_str:
        return FailureScenario.LLM_TIMEOUT

    if (
        "rate limit" in error_str
        or "429" in error_str
        or "too many requests" in error_str
    ):
        return FailureScenario.LLM_ERROR

    if any(
        kw in error_str
        for kw in (
            "context length",
            "token limit",
            "maximum context",
            "too many tokens",
            "context_length_exceeded",
        )
    ):
        return FailureScenario.CONTEXT_OVERFLOW

    if "corrupt" in error_str or "malformed" in error_str:
        return FailureScenario.MEMORY_CORRUPTION

    if any(
        kw in error_str
        for kw in (
            "connection refused",
            "connection reset",
            "503",
            "502",
            "service unavailable",
            "bad gateway",
        )
    ):
        return FailureScenario.PROVIDER_FAILURE

    if (
        "401" in error_str
        or "unauthorized" in error_str
        or "authentication" in error_str
    ):
        return FailureScenario.PROVIDER_FAILURE

    if "tool" in error_str and ("error" in error_str or "failed" in error_str):
        return FailureScenario.TOOL_EXECUTION_ERROR

    if any(
        kw in error_str
        for kw in (
            "dependency",
            "downstream",
            "upstream",
            "external service",
            "dependent",
            "dependency timeout",
            "database",
            "sqlite",
            "disk",
            "filesystem",
            "file system",
            "i/o error",
            "io error",
            "input/output",
            "no space left",
            "read-only",
        )
    ):
        return FailureScenario.DEPENDENCY_TIMEOUT

    return FailureScenario.LLM_ERROR
