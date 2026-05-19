"""Tests for agent_patterns.recovery_recipes"""

import json

from techrevati.runtime.retry_policy import (
    EscalationPolicy,
    FailureScenario,
    RecoveryContext,
    RecoveryResult,
    attempt_recovery,
    backoff_delay,
    classify_exception,
    next_provider,
    recipe_for,
    smaller_context_budget,
)


class FailingRecoveryContext(RecoveryContext):
    """Test-only context that injects partial-recovery failures."""

    def __init__(self, fail_at_step: int) -> None:
        super().__init__()
        self._fail_at = fail_at_step

    def _fail_at_step(self) -> int | None:  # type: ignore[override]
        return self._fail_at


def test_each_scenario_has_recipe():
    for scenario in FailureScenario:
        recipe = recipe_for(scenario)
        assert len(recipe.steps) > 0
        assert recipe.max_attempts >= 1


def test_successful_recovery():
    ctx = RecoveryContext()
    result = attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    assert result.outcome == "recovered"
    assert result.steps_taken > 0
    assert len(ctx.events) == 1
    assert ctx.events[0].event_type == "succeeded"


def test_escalation_after_max_attempts():
    ctx = RecoveryContext()
    # LLM_TIMEOUT has max_attempts=2
    attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    result = attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    assert result.outcome == "escalation_required"
    assert "exhausted" in result.reason


def test_partial_recovery_on_step_failure():
    ctx = FailingRecoveryContext(fail_at_step=1)  # fail at second step
    result = attempt_recovery(FailureScenario.CONTEXT_OVERFLOW, ctx)
    assert result.outcome == "partial_recovery"
    assert len(result.recovered_steps) == 1
    assert len(result.remaining_steps) >= 1


def test_first_step_failure():
    ctx = FailingRecoveryContext(fail_at_step=0)
    result = attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    assert result.outcome == "partial_recovery"
    assert result.steps_taken == 0


def test_context_tracks_per_scenario():
    ctx = RecoveryContext()
    attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    attempt_recovery(FailureScenario.LLM_ERROR, ctx)
    assert ctx.attempt_count(FailureScenario.LLM_TIMEOUT) == 1
    assert ctx.attempt_count(FailureScenario.LLM_ERROR) == 1
    assert ctx.attempt_count(FailureScenario.PROVIDER_FAILURE) == 0


def test_classify_timeout():
    assert classify_exception(TimeoutError()) == FailureScenario.LLM_TIMEOUT
    assert classify_exception(TimeoutError()) == FailureScenario.LLM_TIMEOUT


def test_classify_rate_limit():
    assert (
        classify_exception(Exception("429 Too Many Requests"))
        == FailureScenario.LLM_ERROR
    )
    assert (
        classify_exception(Exception("rate limit exceeded"))
        == FailureScenario.LLM_ERROR
    )


def test_classify_context_overflow():
    assert (
        classify_exception(Exception("context_length_exceeded"))
        == FailureScenario.CONTEXT_OVERFLOW
    )
    assert (
        classify_exception(Exception("too many tokens"))
        == FailureScenario.CONTEXT_OVERFLOW
    )


def test_classify_memory_corruption():
    assert (
        classify_exception(json.JSONDecodeError("x", "y", 0))
        == FailureScenario.MEMORY_CORRUPTION
    )


def test_classify_connection_error():
    assert (
        classify_exception(ConnectionRefusedError()) == FailureScenario.PROVIDER_FAILURE
    )
    assert (
        classify_exception(Exception("503 service unavailable"))
        == FailureScenario.PROVIDER_FAILURE
    )


def test_classify_default():
    assert classify_exception(Exception("unknown error")) == FailureScenario.LLM_ERROR


def test_backoff_delay():
    d0 = backoff_delay(0, jitter=False)
    d1 = backoff_delay(1, jitter=False)
    assert d0 == 1.0  # 2^0
    assert d1 == 2.0  # 2^1


def test_next_provider():
    assert next_provider(["claude", "gpt4", "test-local"], "claude") == "gpt4"
    assert next_provider(["claude"], "claude") is None
    assert next_provider([], "claude") is None


def test_smaller_context_budget():
    assert smaller_context_budget(10000) == 7500
    assert smaller_context_budget(10000, 0.5) == 5000


def test_escalation_policies():
    recipe = recipe_for(FailureScenario.MEMORY_CORRUPTION)
    assert recipe.escalation_policy == EscalationPolicy.ABORT

    recipe = recipe_for(FailureScenario.TOOL_EXECUTION_ERROR)
    assert recipe.escalation_policy == EscalationPolicy.LOG_AND_CONTINUE


def test_result_to_dict():
    result = RecoveryResult(
        outcome="recovered", steps_taken=2, recovered_steps=["a", "b"]
    )
    d = result.to_dict()
    assert d["outcome"] == "recovered"
    assert d["steps_taken"] == 2
