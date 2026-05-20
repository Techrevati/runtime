"""Regression tests for Sprint 0 bug fixes.

Each test pins behavior that the pre-S0 code got wrong and that a future
change could plausibly re-break. Grouped by K-id so the failure message
points back to the audit entry.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time

import pytest

from techrevati.runtime import (
    FailureScenario,
    OrchestrationSession,
    Orchestrator,
    RecoveryContext,
    RecoveryRecipe,
    RecoveryStep,
    TurnTimeoutError,
    UsageSnapshot,
    UsageTracker,
    __version__,
    attempt_recovery,
    classify_exception,
    register_pricing,
)
from techrevati.runtime.usage_tracking import (
    PRICING_TABLE,
    ModelPricing,
    _resolve_pricing,
)

# -- K7: classify_exception walks __cause__ / __context__ ---------------


def test_classify_exception_walks_explicit_cause() -> None:
    """Wrapped exceptions are classified by the original cause's type."""
    try:
        try:
            raise ConnectionRefusedError("provider down")
        except ConnectionRefusedError as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as wrapper:
        assert classify_exception(wrapper) is FailureScenario.PROVIDER_FAILURE


def test_classify_exception_walks_implicit_context() -> None:
    """Implicit __context__ is also followed when __cause__ is absent."""
    # With `from None`: __suppress_context__ is set, walk stops at the
    # outermost exception. Plain wrapper message → falls through to the
    # default LLM_ERROR bucket.
    try:
        try:
            raise TimeoutError("provider exceeded deadline")
        except TimeoutError:
            raise ValueError("wrapper message") from None
    except ValueError as e:
        assert classify_exception(e) is FailureScenario.LLM_ERROR

    # Without an explicit chain: Python sets __context__ implicitly to
    # the in-flight TimeoutError. The classifier walks into it via
    # __context__ and classifies as LLM_TIMEOUT.
    inner_exc = TimeoutError("provider exceeded deadline")
    outer_exc = ValueError("wrapper message")
    outer_exc.__context__ = inner_exc  # type: ignore[assignment]
    assert classify_exception(outer_exc) is FailureScenario.LLM_TIMEOUT


def test_classify_exception_handles_cyclic_chain() -> None:
    """A self-referential exception chain must not loop forever."""
    a = RuntimeError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    # We don't care which scenario wins — just that the call returns.
    classify_exception(a)


def test_classify_exception_json_decode_still_matches() -> None:
    """Direct stdlib type-match still works after the type-mapping refactor."""
    try:
        json.loads("{ not json")
    except json.JSONDecodeError as e:
        assert classify_exception(e) is FailureScenario.MEMORY_CORRUPTION


# -- K8: RecoveryRecipe.steps is a tuple --------------------------------


def test_recovery_recipe_steps_are_tuple() -> None:
    recipe = RecoveryRecipe(
        scenario=FailureScenario.LLM_TIMEOUT,
        steps=[RecoveryStep.RETRY_WITH_BACKOFF],  # accept list at the call site
        max_attempts=2,
        escalation_policy=__import__(
            "techrevati.runtime", fromlist=["EscalationPolicy"]
        ).EscalationPolicy.LOG_AND_CONTINUE,
    )
    # __post_init__ freezes to tuple regardless of input type.
    assert isinstance(recipe.steps, tuple)


def test_attempt_recovery_uses_tuple_steps() -> None:
    """attempt_recovery still walks recipe.steps without mutating it."""
    ctx = RecoveryContext()
    result = attempt_recovery(FailureScenario.LLM_ERROR, ctx)
    assert result.outcome == "recovered"
    assert result.steps_taken == 2


# -- K4: _resolve_pricing is safe under concurrent mutation --------------


def test_resolve_pricing_safe_under_concurrent_register() -> None:
    """Reads must not raise ``dictionary changed size during iteration``.

    Without the snapshot-under-lock fix, this test fails intermittently
    on busy hardware.
    """
    stop = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        i = 0
        while not stop.is_set():
            register_pricing(f"churn-model-{i % 50}", ModelPricing(1.0, 2.0))
            i += 1

    def reader() -> None:
        try:
            for _ in range(2000):
                _resolve_pricing("never-registered-prefix-xyz")
        except Exception as exc:  # noqa: BLE001 — propagate to assertion
            errors.append(exc)

    writers = [threading.Thread(target=writer, daemon=True) for _ in range(4)]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in writers + readers:
        t.start()
    for t in readers:
        t.join(timeout=10.0)
    stop.set()
    for t in writers:
        t.join(timeout=2.0)
    # Cleanup the churn entries we created.
    for i in range(50):
        PRICING_TABLE.pop(f"churn-model-{i}", None)
    assert not errors, errors


# -- K5: timeout returns promptly without blocking on slow thread --------


def test_run_turn_timeout_does_not_block_on_slow_fn() -> None:
    """Hard turn timeout must return immediately, not wait for fn to finish.

    Pre-S0 used ``with ThreadPoolExecutor(...) as ex:`` which exits via
    ``shutdown(wait=True)`` — i.e. the timeout effectively waited for the
    slow function to return. The fix bypasses the context manager and
    calls ``shutdown(wait=False, cancel_futures=True)`` in finally.
    """
    orch = Orchestrator(role="writer", phase="draft")

    def slow_fn() -> str:
        time.sleep(2.0)
        return "ok"

    started = time.monotonic()
    with orch.session() as session:
        with pytest.raises(TurnTimeoutError):
            session.run_turn(slow_fn, timeout=0.1)
    elapsed = time.monotonic() - started
    # Generous bound: the timeout itself is 100ms; allow 500ms for CI noise.
    # The pre-fix code would block ~2s waiting for slow_fn to return.
    assert elapsed < 0.5, f"timeout blocked for {elapsed:.3f}s — K5 regression"


# -- K9: __version__ is sourced from package metadata --------------------


def test_version_is_a_pep440_like_string() -> None:
    """Whatever the source, __version__ must be a non-empty string."""
    assert isinstance(__version__, str)
    assert __version__
    # Local-checkout fallback or installed metadata — both look version-y.
    assert any(c.isdigit() for c in __version__)


# -- Smoke: pre-existing async path still works after orchestrator edits --


@pytest.mark.asyncio
async def test_arun_turn_smoke_still_passes() -> None:
    orch = Orchestrator(role="writer", phase="draft")

    async def fast_coro() -> str:
        await asyncio.sleep(0)
        return "ok"

    async with orch.asession() as session:
        result, usage = await session.arun_turn(
            fast_coro,
            model="",
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
        )
    assert result == "ok"
    assert usage.input_tokens == 10


# -- Smoke: pricing table read path still works --------------------------


def test_usage_tracker_uses_resolve_pricing() -> None:
    register_pricing("smoke-model-x", ModelPricing(3.0, 6.0))
    tracker = UsageTracker()
    cost = tracker.cost_for_turn(
        "smoke-model-x", UsageSnapshot(input_tokens=1_000_000, output_tokens=500_000)
    )
    # 1M * 3.0/1M + 0.5M * 6.0/1M = 3.0 + 3.0 = 6.0
    assert cost == pytest.approx(6.0)
    PRICING_TABLE.pop("smoke-model-x", None)


# -- Sanity: futures.cancel + shutdown stay compatible with stdlib -------


def test_thread_pool_shutdown_cancel_futures_signature_exists() -> None:
    """Guard against accidental Python downgrade — cancel_futures is 3.9+."""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        ex.shutdown(wait=False, cancel_futures=True)
    finally:
        # Already shut down above; calling again is harmless.
        ex.shutdown(wait=False)


# Avoid leaking the orchestrator import for OrchestrationSession at module
# scope when consumers only need the public re-exports.
_ = OrchestrationSession  # keep the import resolved for static analysis
