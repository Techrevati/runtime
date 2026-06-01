"""Tests for agent_patterns.recovery_recipes"""

import asyncio
import json
from typing import Any, cast

import pytest

from techrevati.runtime.retry_policy import (
    EscalationPolicy,
    FailureScenario,
    RecoveryContext,
    RecoveryEvent,
    RecoveryRecipe,
    RecoveryResult,
    RecoveryStep,
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


def test_recipe_for_accepts_string_scenario():
    recipe = recipe_for("llm_timeout")
    assert recipe.scenario == FailureScenario.LLM_TIMEOUT


def test_recovery_recipe_rejects_invalid_shape():
    with pytest.raises(ValueError, match="scenario"):
        RecoveryRecipe(
            scenario=cast(Any, "missing"),
            steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
            max_attempts=1,
            escalation_policy=EscalationPolicy.ALERT_HUMAN,
        )
    with pytest.raises(ValueError, match="steps"):
        RecoveryRecipe(
            scenario=FailureScenario.LLM_TIMEOUT,
            steps=cast(Any, ()),
            max_attempts=1,
            escalation_policy=EscalationPolicy.ALERT_HUMAN,
        )
    with pytest.raises(TypeError, match="steps"):
        RecoveryRecipe(
            scenario=FailureScenario.LLM_TIMEOUT,
            steps=cast(Any, "retry_with_backoff"),
            max_attempts=1,
            escalation_policy=EscalationPolicy.ALERT_HUMAN,
        )
    with pytest.raises(ValueError, match="max_attempts"):
        RecoveryRecipe(
            scenario=FailureScenario.LLM_TIMEOUT,
            steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
            max_attempts=0,
            escalation_policy=EscalationPolicy.ALERT_HUMAN,
        )
    with pytest.raises(ValueError, match="escalation_policy"):
        RecoveryRecipe(
            scenario=FailureScenario.LLM_TIMEOUT,
            steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
            max_attempts=1,
            escalation_policy=cast(Any, "teleport"),
        )
    with pytest.raises(ValueError, match="step_retries"):
        RecoveryRecipe(
            scenario=FailureScenario.LLM_TIMEOUT,
            steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
            max_attempts=1,
            escalation_policy=EscalationPolicy.ALERT_HUMAN,
            step_retries={RecoveryStep.SWITCH_PROVIDER: 1},
        )
    with pytest.raises(ValueError, match="step_retries budget"):
        RecoveryRecipe(
            scenario=FailureScenario.LLM_TIMEOUT,
            steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
            max_attempts=1,
            escalation_policy=EscalationPolicy.ALERT_HUMAN,
            step_retries={RecoveryStep.RETRY_WITH_BACKOFF: -1},
        )


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
    assert ctx.attempt_count("provider_failure") == 0


def test_attempt_recovery_rejects_invalid_context():
    with pytest.raises(TypeError, match="RecoveryContext"):
        attempt_recovery(FailureScenario.LLM_TIMEOUT, cast(Any, object()))


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


def test_classify_local_dependency_io_failures():
    assert classify_exception(OSError("No space left on device")) == (
        FailureScenario.DEPENDENCY_TIMEOUT
    )
    assert classify_exception(Exception("database is locked")) == (
        FailureScenario.DEPENDENCY_TIMEOUT
    )

    try:
        raise RuntimeError("wrapped storage failure") from OSError("disk I/O error")
    except RuntimeError as exc:
        wrapped = exc

    assert classify_exception(wrapped) == FailureScenario.DEPENDENCY_TIMEOUT


def test_classify_default():
    assert classify_exception(Exception("unknown error")) == FailureScenario.LLM_ERROR


def test_backoff_delay_no_jitter():
    """jitter=False (bool) maps to 'none' mode: pure exponential."""
    assert backoff_delay(0, jitter=False) == 1.0  # 2^0
    assert backoff_delay(1, jitter=False) == 2.0  # 2^1
    assert backoff_delay(0, jitter="none") == 1.0


def test_backoff_delay_full_jitter_bounded():
    """Full jitter returns uniform in [0, base**attempt]."""
    for _ in range(50):
        d = backoff_delay(3, base=2.0, jitter="full")
        assert 0.0 <= d <= 8.0


def test_backoff_delay_equal_jitter_bounded():
    """Equal jitter returns half deterministic + half random."""
    for _ in range(50):
        d = backoff_delay(3, base=2.0, jitter="equal")
        assert 4.0 <= d <= 8.0


def test_backoff_delay_decorrelated_bounded():
    """Decorrelated jitter returns uniform in [base, prev*3], respects cap."""
    for _ in range(50):
        d = backoff_delay(3, base=2.0, jitter="decorrelated", prev_delay=4.0)
        assert 2.0 <= d <= 12.0
        assert d <= 60.0  # cap


def test_backoff_delay_cap_applies():
    """Cap clamps the exponential before randomization."""
    # 2 ** 20 = 1048576; cap=10 should clamp.
    for _ in range(20):
        d = backoff_delay(20, base=2.0, jitter="full", cap=10.0)
        assert d <= 10.0


def test_backoff_delay_bool_true_maps_to_full():
    """jitter=True (bool) is backwards-compat for 'full' mode."""
    for _ in range(20):
        d = backoff_delay(2, base=2.0, jitter=True)
        assert 0.0 <= d <= 4.0


def test_backoff_delay_invalid_mode_raises():
    with pytest.raises(ValueError, match="unknown jitter mode"):
        backoff_delay(1, jitter="quantum")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "error_type", "match"),
    [
        ({"attempt": True}, TypeError, "attempt"),
        ({"attempt": -1}, ValueError, "attempt"),
        ({"attempt": 1, "base": 0.0}, ValueError, "base"),
        ({"attempt": 1, "base": float("nan")}, ValueError, "base"),
        ({"attempt": 1, "cap": -1.0}, ValueError, "cap"),
        ({"attempt": 1, "cap": float("inf")}, ValueError, "cap"),
        ({"attempt": 1, "prev_delay": -1.0}, ValueError, "prev_delay"),
    ],
)
def test_backoff_delay_rejects_invalid_numbers(
    kwargs: dict[str, Any], error_type: type[Exception], match: str
):
    with pytest.raises(error_type, match=match):
        backoff_delay(**kwargs)


def test_backoff_delay_handles_huge_attempt_by_capping():
    assert backoff_delay(100_000, jitter=False, cap=10.0) == 10.0


def test_next_provider():
    assert next_provider(["provider-a", "gpt4", "test-local"], "provider-a") == "gpt4"
    assert next_provider(["provider-a"], "provider-a") is None
    assert next_provider([], "provider-a") is None


def test_next_provider_rejects_invalid_names():
    with pytest.raises(TypeError, match="sequence"):
        next_provider(cast(Any, "provider-a"), "provider-a")
    with pytest.raises(ValueError, match="current_provider"):
        next_provider([], "")
    with pytest.raises(TypeError, match="available_providers"):
        next_provider(cast(Any, [1]), "provider-a")
    with pytest.raises(ValueError, match="available_providers"):
        next_provider([""], "provider-a")


def test_smaller_context_budget():
    assert smaller_context_budget(10000) == 7500
    assert smaller_context_budget(10000, 0.5) == 5000


@pytest.mark.parametrize(
    ("kwargs", "error_type", "match"),
    [
        ({"current_chars": True}, TypeError, "current_chars"),
        ({"current_chars": -1}, ValueError, "current_chars"),
        ({"current_chars": 100, "reduction": float("nan")}, ValueError, "reduction"),
        ({"current_chars": 100, "reduction": -0.1}, ValueError, "between 0 and 1"),
        ({"current_chars": 100, "reduction": 1.1}, ValueError, "between 0 and 1"),
    ],
)
def test_smaller_context_budget_rejects_invalid_values(
    kwargs: dict[str, Any], error_type: type[Exception], match: str
):
    with pytest.raises(error_type, match=match):
        smaller_context_budget(**kwargs)


def test_escalation_policies():
    recipe = recipe_for(FailureScenario.MEMORY_CORRUPTION)
    assert recipe.escalation_policy == EscalationPolicy.ABORT


# -- Async recovery (Sprint 2.5) --


def test_aattempt_recovery_matches_sync_behavior():
    """Async variant returns the same result as the sync function."""
    from techrevati.runtime.retry_policy import aattempt_recovery

    sync_ctx = RecoveryContext()
    async_ctx = RecoveryContext()

    sync_result = attempt_recovery(FailureScenario.LLM_TIMEOUT, sync_ctx)
    async_result = asyncio.run(
        aattempt_recovery(FailureScenario.LLM_TIMEOUT, async_ctx)
    )

    assert sync_result.outcome == async_result.outcome
    assert sync_result.recovered_steps == async_result.recovered_steps
    assert sync_ctx.attempt_count(
        FailureScenario.LLM_TIMEOUT
    ) == async_ctx.attempt_count(FailureScenario.LLM_TIMEOUT)


def test_aattempt_recovery_accepts_custom_sleeper():
    """sleeper parameter is accepted and may be a no-op."""
    from techrevati.runtime.retry_policy import aattempt_recovery

    called: list[float] = []

    async def fake_sleeper(secs: float) -> None:
        called.append(secs)

    ctx = RecoveryContext()
    result = asyncio.run(
        aattempt_recovery(FailureScenario.LLM_TIMEOUT, ctx, sleeper=fake_sleeper)
    )
    assert result.outcome == "recovered"
    # No step in the current recipe sleeps, so the sleeper isn't called.
    # The contract is the parameter is accepted without TypeError.
    assert called == []

    recipe = recipe_for(FailureScenario.TOOL_EXECUTION_ERROR)
    assert recipe.escalation_policy == EscalationPolicy.LOG_AND_CONTINUE


def test_result_to_dict():
    result = RecoveryResult(
        outcome="recovered", steps_taken=2, recovered_steps=["a", "b"]
    )
    d = result.to_dict()
    assert d["outcome"] == "recovered"
    assert d["steps_taken"] == 2


def test_result_and_event_copy_mutable_fields():
    recovered = ["a"]
    result = RecoveryResult(outcome="recovered", recovered_steps=recovered)
    recovered.append("later")
    assert result.recovered_steps == ["a"]
    d = result.to_dict()
    d["recovered_steps"].append("extra")
    assert result.recovered_steps == ["a"]

    result_payload = {"outcome": "recovered", "nested": {"values": [1]}}
    event = RecoveryEvent(
        event_type="succeeded",
        scenario="llm_timeout",
        recipe_steps=["retry_with_backoff"],
        result=result_payload,
        timestamp="2026-01-01T00:00:00+00:00",
    )
    result_payload["later"] = True
    result_payload["nested"]["values"].append(2)
    assert event.result == {"outcome": "recovered", "nested": {"values": [1]}}
    event_dict = event.to_dict()
    event_dict["result"]["extra"] = True
    event_dict["result"]["nested"]["values"].append(3)
    assert event.result == {"outcome": "recovered", "nested": {"values": [1]}}


def test_recovery_context_events_are_snapshots():
    ctx = RecoveryContext()
    attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)

    first_read = ctx.events
    assert first_read
    first_read[0].result["recovered_steps"].append("mutated")

    second_read = ctx.events
    assert second_read[0].result["recovered_steps"] == [
        RecoveryStep.RETRY_WITH_BACKOFF.value
    ]
    assert second_read[0] is not first_read[0]


def test_result_and_event_reject_invalid_shape():
    with pytest.raises(ValueError, match="outcome"):
        RecoveryResult(outcome=cast(Any, "maybe"))
    with pytest.raises(TypeError, match="steps_taken"):
        RecoveryResult(outcome="recovered", steps_taken=cast(Any, True))
    with pytest.raises(TypeError, match="recovered_steps"):
        RecoveryResult(outcome="recovered", recovered_steps=cast(Any, ()))
    with pytest.raises(ValueError, match="event_type"):
        RecoveryEvent(
            event_type="unknown",
            scenario="llm_timeout",
            recipe_steps=["retry_with_backoff"],
            result={},
            timestamp="2026-01-01T00:00:00+00:00",
        )
    with pytest.raises(TypeError, match="result"):
        RecoveryEvent(
            event_type="succeeded",
            scenario="llm_timeout",
            recipe_steps=["retry_with_backoff"],
            result=cast(Any, []),
            timestamp="2026-01-01T00:00:00+00:00",
        )
