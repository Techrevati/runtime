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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

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


@dataclass(frozen=True)
class RecoveryRecipe:
    """Recovery plan for a failure scenario.

    ``step_retries`` is an optional per-step retry budget the caller is
    expected to honor when actually executing a step. The default empty
    mapping preserves the 0.1.0 single-attempt semantics; populate it
    when you want, e.g. ``RETRY_WITH_BACKOFF`` to fire three times
    before the recipe moves on to ``SWITCH_PROVIDER``. This module
    only records the budget — execution happens in the caller.
    """

    scenario: FailureScenario
    steps: tuple[RecoveryStep, ...]
    max_attempts: int
    escalation_policy: EscalationPolicy
    step_retries: dict[RecoveryStep, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Accept list/tuple at the call site; freeze to tuple so the
        # frozen-dataclass contract is honored end-to-end.
        if not isinstance(self.steps, tuple):
            object.__setattr__(self, "steps", tuple(self.steps))


@dataclass
class RecoveryResult:
    """Outcome of a recovery attempt."""

    outcome: Literal["recovered", "partial_recovery", "escalation_required"]
    steps_taken: int = 0
    recovered_steps: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "steps_taken": self.steps_taken,
            "recovered_steps": self.recovered_steps,
            "remaining_steps": self.remaining_steps,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "scenario": self.scenario,
            "recipe_steps": self.recipe_steps,
            "result": self.result,
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


def recipe_for(scenario: FailureScenario) -> RecoveryRecipe:
    """Look up the recovery recipe for a failure scenario."""
    return _RECIPES[scenario]


class RecoveryContext:
    """Tracks recovery attempts per scenario within a session."""

    def __init__(self) -> None:
        self._attempts: dict[FailureScenario, int] = {}
        self._events: list[RecoveryEvent] = []

    def attempt_count(self, scenario: FailureScenario) -> int:
        return self._attempts.get(scenario, 0)

    @property
    def events(self) -> list[RecoveryEvent]:
        return list(self._events)

    def _fail_at_step(self) -> int | None:
        """Override in test subclasses to simulate partial recovery."""
        return None

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
    scenario: FailureScenario,
    ctx: RecoveryContext,
) -> RecoveryResult:
    """Attempt recovery for a failure scenario.

    Returns RecoveryResult with outcome: recovered, partial_recovery,
    or escalation_required.
    """
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

    # Execute steps
    recovered_steps: list[str] = []
    remaining_steps: list[str] = []
    fail_at = ctx._fail_at_step()

    for i, step in enumerate(recipe.steps):
        if fail_at is not None and i >= fail_at:
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
    scenario: FailureScenario,
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
    if jitter is True:
        mode: JitterMode = "full"
    elif jitter is False:
        mode = "none"
    else:
        mode = jitter

    cap_exp = min(cap, base**attempt)

    if mode == "none":
        return cap_exp
    if mode == "full":
        return random.uniform(0.0, cap_exp)
    if mode == "equal":
        half = cap_exp / 2.0
        return half + random.uniform(0.0, half)
    if mode == "decorrelated":
        anchor = prev_delay if prev_delay > 0 else base
        return min(cap, random.uniform(base, anchor * 3.0))
    raise ValueError(f"unknown jitter mode: {mode!r}")


def next_provider(
    available_providers: list[str],
    current_provider: str,
) -> str | None:
    """Select the next fallback provider, skipping the current one."""
    candidates = [p for p in available_providers if p != current_provider]
    return candidates[0] if candidates else None


def smaller_context_budget(current_chars: int, reduction: float = 0.75) -> int:
    """Calculate a reduced context budget (75% of current by default)."""
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
       (``TimeoutError``, ``ConnectionError`` family, ``JSONDecodeError``).
       Walks the exception chain via ``__cause__`` / ``__context__`` so a
       ``RuntimeError`` wrapping a ``ConnectionError`` is still classified
       as ``PROVIDER_FAILURE``.
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
        )
    ):
        return FailureScenario.DEPENDENCY_TIMEOUT

    return FailureScenario.LLM_ERROR
