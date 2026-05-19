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
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    """Recovery plan for a failure scenario."""
    scenario: FailureScenario
    steps: list[RecoveryStep]
    max_attempts: int
    escalation_policy: EscalationPolicy


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
        steps=[RecoveryStep.RETRY_WITH_BACKOFF],
        max_attempts=2,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.LLM_ERROR: RecoveryRecipe(
        scenario=FailureScenario.LLM_ERROR,
        steps=[RecoveryStep.RETRY_WITH_BACKOFF, RecoveryStep.SWITCH_PROVIDER],
        max_attempts=2,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.TOOL_EXECUTION_ERROR: RecoveryRecipe(
        scenario=FailureScenario.TOOL_EXECUTION_ERROR,
        steps=[RecoveryStep.RESTART_AGENT],
        max_attempts=1,
        escalation_policy=EscalationPolicy.LOG_AND_CONTINUE,
    ),
    FailureScenario.CONTEXT_OVERFLOW: RecoveryRecipe(
        scenario=FailureScenario.CONTEXT_OVERFLOW,
        steps=[RecoveryStep.RETRY_WITH_SMALLER_CONTEXT, RecoveryStep.REDUCE_TOOL_SET],
        max_attempts=1,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.DEPENDENCY_TIMEOUT: RecoveryRecipe(
        scenario=FailureScenario.DEPENDENCY_TIMEOUT,
        steps=[RecoveryStep.RETRY_WITH_BACKOFF],
        max_attempts=1,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
    ),
    FailureScenario.MEMORY_CORRUPTION: RecoveryRecipe(
        scenario=FailureScenario.MEMORY_CORRUPTION,
        steps=[RecoveryStep.CLEAR_MEMORY_CACHE, RecoveryStep.RESTART_AGENT],
        max_attempts=1,
        escalation_policy=EscalationPolicy.ABORT,
    ),
    FailureScenario.PROVIDER_FAILURE: RecoveryRecipe(
        scenario=FailureScenario.PROVIDER_FAILURE,
        steps=[RecoveryStep.SWITCH_PROVIDER, RecoveryStep.RETRY_WITH_BACKOFF],
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

    def _emit(self, event_type: str, scenario: FailureScenario,
              recipe: RecoveryRecipe, result: RecoveryResult) -> None:
        self._events.append(RecoveryEvent(
            event_type=event_type,
            scenario=scenario.value,
            recipe_steps=[s.value for s in recipe.steps],
            result=result.to_dict(),
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))


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
            reason=f"max attempts ({recipe.max_attempts}) exhausted for {scenario.value}",
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


# -- Step handlers (actual implementations) --

def backoff_delay(attempt: int, base: float = 2.0, jitter: bool = True) -> float:
    """Calculate backoff delay in seconds with optional jitter."""
    delay = base ** attempt
    if jitter:
        delay += random.uniform(0, delay * 0.25)
    return delay


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

def classify_exception(error: Exception) -> FailureScenario:
    """Map a Python exception to a FailureScenario for recovery."""
    error_str = str(error).lower()

    # Timeout errors
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return FailureScenario.LLM_TIMEOUT
    if "timeout" in error_str or "timed out" in error_str:
        return FailureScenario.LLM_TIMEOUT

    # Rate limits
    if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
        return FailureScenario.LLM_ERROR

    # Context overflow
    if any(kw in error_str for kw in [
        "context length", "token limit", "maximum context",
        "too many tokens", "context_length_exceeded",
    ]):
        return FailureScenario.CONTEXT_OVERFLOW

    # Memory corruption
    if isinstance(error, (json.JSONDecodeError,)):
        return FailureScenario.MEMORY_CORRUPTION
    if "corrupt" in error_str or "malformed" in error_str:
        return FailureScenario.MEMORY_CORRUPTION

    # Connection / provider failures
    if isinstance(error, (ConnectionError, ConnectionRefusedError, ConnectionResetError)):
        return FailureScenario.PROVIDER_FAILURE
    if any(kw in error_str for kw in [
        "connection refused", "connection reset", "503", "502",
        "service unavailable", "bad gateway",
    ]):
        return FailureScenario.PROVIDER_FAILURE

    # Authentication errors
    if "401" in error_str or "unauthorized" in error_str or "authentication" in error_str:
        return FailureScenario.PROVIDER_FAILURE

    # Tool errors
    if "tool" in error_str and ("error" in error_str or "failed" in error_str):
        return FailureScenario.TOOL_EXECUTION_ERROR

    # Dependency/downstream timeouts
    if any(kw in error_str for kw in [
        "dependency", "downstream", "upstream", "external service",
        "dependent", "dependency timeout",
    ]):
        return FailureScenario.DEPENDENCY_TIMEOUT

    # Default to LLM error
    return FailureScenario.LLM_ERROR
