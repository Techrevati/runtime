"""RecoveryRecipe.step_retries — per-step retry budget execution."""

from __future__ import annotations

import asyncio

import pytest

from techrevati.runtime.retry_policy import (
    _RECIPES,
    EscalationPolicy,
    FailureScenario,
    RecoveryContext,
    RecoveryRecipe,
    RecoveryStep,
    aattempt_recovery,
    attempt_recovery,
)


class FlakySingleStepContext(RecoveryContext):
    """Fails the first N attempts of step 0, then succeeds."""

    def __init__(self, fail_first_n_attempts: int) -> None:
        super().__init__()
        self._fail_n = fail_first_n_attempts

    def _fail_at_attempt(
        self, step: RecoveryStep, step_index: int, attempt: int
    ) -> bool:
        if step_index != 0:
            return False
        return attempt < self._fail_n


def _set_recipe(recipe: RecoveryRecipe) -> None:
    """Swap a recipe into the global table for the duration of one test."""
    _RECIPES[recipe.scenario] = recipe


def _restore_recipe(scenario: FailureScenario, original: RecoveryRecipe) -> None:
    _RECIPES[scenario] = original


@pytest.fixture
def llm_timeout_with_retry_budget():
    """LLM_TIMEOUT recipe with a per-step retry budget of 3 on the first step."""
    original = _RECIPES[FailureScenario.LLM_TIMEOUT]
    custom = RecoveryRecipe(
        scenario=FailureScenario.LLM_TIMEOUT,
        steps=(RecoveryStep.RETRY_WITH_BACKOFF, RecoveryStep.SWITCH_PROVIDER),
        max_attempts=5,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
        step_retries={RecoveryStep.RETRY_WITH_BACKOFF: 3},
    )
    _set_recipe(custom)
    yield custom
    _restore_recipe(FailureScenario.LLM_TIMEOUT, original)


def test_step_retries_succeeds_within_budget(llm_timeout_with_retry_budget):
    """When the first two attempts fail and the third succeeds, recovery proceeds."""
    ctx = FlakySingleStepContext(fail_first_n_attempts=2)
    result = attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    assert result.outcome == "recovered"
    assert result.steps_taken == 2
    assert result.recovered_steps == ["retry_with_backoff", "switch_provider"]


def test_step_retries_exhausts_budget_then_moves_to_partial(
    llm_timeout_with_retry_budget,
):
    """When all 3 attempts fail, the step is abandoned (partial recovery)."""
    ctx = FlakySingleStepContext(fail_first_n_attempts=3)
    result = attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
    assert result.outcome == "partial_recovery"
    assert result.steps_taken == 0
    assert result.recovered_steps == []
    assert result.remaining_steps == ["retry_with_backoff", "switch_provider"]


def test_step_retries_default_one_attempt_preserves_legacy_semantics():
    """Recipes without step_retries behave exactly as in 0.2.0."""
    ctx = FlakySingleStepContext(fail_first_n_attempts=1)
    # CONTEXT_OVERFLOW has no step_retries set → default budget 1
    result = attempt_recovery(FailureScenario.CONTEXT_OVERFLOW, ctx)
    assert result.outcome == "partial_recovery"


def test_aattempt_recovery_honors_step_retries(llm_timeout_with_retry_budget):
    """Async executor must match sync semantics for per-step budgets."""
    ctx = FlakySingleStepContext(fail_first_n_attempts=2)
    result = asyncio.run(aattempt_recovery(FailureScenario.LLM_TIMEOUT, ctx))
    assert result.outcome == "recovered"
    assert result.steps_taken == 2


def test_step_retries_zero_budget_skips_step():
    """step_retries=0 means do not even try; treat as immediate failure."""
    original = _RECIPES[FailureScenario.LLM_TIMEOUT]
    custom = RecoveryRecipe(
        scenario=FailureScenario.LLM_TIMEOUT,
        steps=(RecoveryStep.RETRY_WITH_BACKOFF,),
        max_attempts=2,
        escalation_policy=EscalationPolicy.ALERT_HUMAN,
        step_retries={RecoveryStep.RETRY_WITH_BACKOFF: 0},
    )
    _set_recipe(custom)
    try:
        ctx = RecoveryContext()  # default never-fail context
        result = attempt_recovery(FailureScenario.LLM_TIMEOUT, ctx)
        assert result.outcome == "partial_recovery"
        assert result.steps_taken == 0
    finally:
        _restore_recipe(FailureScenario.LLM_TIMEOUT, original)
