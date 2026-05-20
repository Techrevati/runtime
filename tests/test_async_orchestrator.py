"""Tests for techrevati.runtime.orchestrator async path (Sprint 2)."""

from __future__ import annotations

import asyncio

import pytest

from techrevati.runtime import (
    AgentStatus,
    AsyncCircuitBreaker,
    AsyncOrchestrationSession,
    BudgetExceededError,
    CircuitOpenError,
    ModelPricing,
    Orchestrator,
    PermissionDeniedError,
    PermissionEnforcer,
    PermissionMode,
    PermissionPolicy,
    RolePermissionConfig,
    TurnTimeoutError,
    UsageSnapshot,
    register_pricing,
)


@pytest.fixture(autouse=True)
def _register_test_pricing():
    register_pricing("test-model", ModelPricing(3.0, 15.0))


# -- arun_turn happy path + lifecycle --


@pytest.mark.asyncio
async def test_asession_happy_path_completes_worker():
    orch = Orchestrator(role="writer", phase="draft", project_id=1)

    async def model_call():
        return "ok"

    async with orch.asession() as session:
        assert isinstance(session, AsyncOrchestrationSession)
        result, usage = await session.arun_turn(
            model_call,
            model="test-model",
            usage=UsageSnapshot(input_tokens=1000, output_tokens=500),
        )
        assert result == "ok"
        assert usage.input_tokens == 1000

    assert session.worker.status == AgentStatus.COMPLETED
    assert session.tracker.total_cost() > 0


@pytest.mark.asyncio
async def test_asession_failure_path_marks_failed():
    orch = Orchestrator(role="writer", phase="draft")

    async def boom():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        async with orch.asession() as session:
            await session.arun_turn(boom)

    assert session.worker.status == AgentStatus.FAILED


# -- arun_turn with async circuit breaker --


@pytest.mark.asyncio
async def test_arun_turn_with_async_circuit_breaker():
    cb = AsyncCircuitBreaker("svc", failure_threshold=1, recovery_timeout_seconds=10)
    orch = Orchestrator(role="writer", phase="draft", async_circuit_breaker=cb)

    async def boom():
        raise RuntimeError("svc down")

    with pytest.raises(RuntimeError):
        async with orch.asession() as session:
            await session.arun_turn(boom)

    # Second session sees the breaker open.
    with pytest.raises(CircuitOpenError):
        async with orch.asession() as session:

            async def quick():
                return "x"

            await session.arun_turn(quick)


# -- Timeouts (item 2.3) --


@pytest.mark.asyncio
async def test_arun_turn_timeout_raises_turn_timeout_error():
    orch = Orchestrator(role="writer", phase="draft")

    async def slow():
        await asyncio.sleep(0.5)
        return "too late"

    with pytest.raises(TurnTimeoutError) as exc_info:
        async with orch.asession() as session:
            await session.arun_turn(slow, timeout=0.05)

    assert exc_info.value.timeout_seconds == 0.05


@pytest.mark.asyncio
async def test_arun_turn_no_timeout_completes():
    orch = Orchestrator(role="writer", phase="draft")

    async def fast():
        return "fast"

    async with orch.asession() as session:
        result, _ = await session.arun_turn(fast, timeout=1.0)
    assert result == "fast"


# -- Tool authorization (item 2.3) --


@pytest.mark.asyncio
async def test_arun_tool_blocks_when_denied():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)
            },
            tool_requirements={"write_db": PermissionMode.FULL_ACCESS},
        )
    )
    orch = Orchestrator(role="reader", phase="draft", permissions=enforcer)

    async def attempt():
        return "leak"

    with pytest.raises(PermissionDeniedError):
        async with orch.asession() as session:
            await session.arun_tool("write_db", attempt)


@pytest.mark.asyncio
async def test_arun_tool_passes_when_allowed():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "writer": RolePermissionConfig("writer", PermissionMode.FULL_ACCESS)
            },
            tool_requirements={"write_db": PermissionMode.FULL_ACCESS},
        )
    )
    orch = Orchestrator(role="writer", phase="draft", permissions=enforcer)

    async def writer():
        return "wrote"

    async with orch.asession() as session:
        assert await session.arun_tool("write_db", writer) == "wrote"


# -- Cancellation → CANCELLED (item 2.4) --


@pytest.mark.asyncio
async def test_cancelled_error_marks_worker_cancelled():
    orch = Orchestrator(role="writer", phase="draft")

    async def slow():
        await asyncio.sleep(10)

    async def run():
        async with orch.asession() as session:
            run.session = session  # type: ignore[attr-defined]
            await session.arun_turn(slow)

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert run.session.worker.status == AgentStatus.CANCELLED  # type: ignore[attr-defined]


# -- Budget enforcement (parity with sync) --


@pytest.mark.asyncio
async def test_arun_turn_respects_enforce_budget():
    orch = Orchestrator(
        role="writer",
        phase="draft",
        budget_usd=0.001,
        enforce_budget=True,
    )

    async def call():
        return "ok"

    with pytest.raises(BudgetExceededError):
        async with orch.asession() as session:
            await session.arun_turn(
                call,
                model="test-model",
                usage=UsageSnapshot(input_tokens=1_000_000),
            )


# -- pause_for_input (item 2.8) --


@pytest.mark.asyncio
async def test_pause_for_input_resumes_with_provided_value():
    orch = Orchestrator(role="writer", phase="draft")

    async def flow():
        async with orch.asession() as session:
            flow.session = session  # type: ignore[attr-defined]
            answer = await session.pause_for_input("approve?")
            return answer

    task = asyncio.create_task(flow())
    # Wait until the session is parked in WAITING_FOR_INPUT.
    for _ in range(100):
        await asyncio.sleep(0.005)
        sess = getattr(flow, "session", None)
        if sess is not None and sess.worker.status == AgentStatus.WAITING_FOR_INPUT:
            break

    flow.session.provide_input("approved")  # type: ignore[attr-defined]
    result = await task
    assert result == "approved"
    assert flow.session.worker.status == AgentStatus.COMPLETED  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_pause_for_input_rejects_double_pause():
    orch = Orchestrator(role="writer", phase="draft")
    async with orch.asession() as session:
        first = asyncio.create_task(session.pause_for_input("a?"))
        await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError, match="still pending"):
            await session.pause_for_input("b?")
        session.provide_input("done")
        await first


# -- Elapsed auto-wiring (item 2.7) --


@pytest.mark.asyncio
async def test_evaluate_policy_auto_elapsed():
    """Without explicit elapsed_seconds, evaluate_policy computes from start."""
    from techrevati.runtime import (
        PolicyAction,
        PolicyActionData,
        PolicyEngine,
        PolicyRule,
    )
    from techrevati.runtime.policy_engine import TimedOut

    timed_out_rule = PolicyRule(
        name="hit-deadline",
        condition=TimedOut(seconds=0.0),  # always true for any elapsed > 0
        actions=[PolicyActionData(PolicyAction.ABORT_PHASE)],
        priority=10,
    )
    orch = Orchestrator(
        role="writer",
        phase="draft",
        policy_engine=PolicyEngine([timed_out_rule]),
    )
    async with orch.asession() as session:
        await asyncio.sleep(0.02)  # ensure elapsed > 0
        actions = session.evaluate_policy()  # no elapsed_seconds — auto
        assert any(a.action == PolicyAction.ABORT_PHASE for a in actions)
