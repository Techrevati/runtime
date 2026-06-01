"""Tests for techrevati.runtime.orchestrator async path (Sprint 2)."""

from __future__ import annotations

import asyncio

import pytest

from techrevati.runtime import (
    AgentFailureClass,
    AgentSession,
    AgentStatus,
    AsyncCircuitBreaker,
    AsyncOrchestrationSession,
    BudgetExceededError,
    CircuitOpenError,
    ModelPricing,
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
    orch = AgentSession(role="writer", phase="draft", project_id=1)

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
    assert session.events[0].event.value == "agent.started"
    assert session.events[0].project_id == 1


@pytest.mark.asyncio
async def test_asession_failure_path_marks_failed():
    orch = AgentSession(role="writer", phase="draft")

    async def boom():
        raise RuntimeError("connection string with sensitive details")

    with pytest.raises(RuntimeError, match="sensitive details"):
        async with orch.asession() as session:
            await session.arun_turn(boom)

    assert session.worker.status == AgentStatus.FAILED
    assert any(e.event.value == "agent.recovery.succeeded" for e in session.events)
    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.LLM_ERROR
    assert terminal.detail == "RuntimeError raised"
    assert "sensitive" not in str(terminal.to_dict())
    assert "sensitive" not in str(session.worker.to_dict())


@pytest.mark.asyncio
async def test_asession_validation_error_marks_terminal_failure_class():
    orch = AgentSession(role="writer", phase="draft")

    async def boom():
        raise TypeError("invalid payload")

    with pytest.raises(TypeError, match="invalid payload"):
        async with orch.asession() as session:
            await session.arun_turn(boom)

    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.VALIDATION_ERROR
    assert terminal.detail == "TypeError raised"
    assert "invalid payload" not in str(terminal.to_dict())


@pytest.mark.asyncio
async def test_asession_prompt_rejection_marks_terminal_failure_class():
    orch = AgentSession(role="writer", phase="draft")

    async def boom():
        raise RuntimeError("content filter blocked the prompt")

    with pytest.raises(RuntimeError, match="content filter"):
        async with orch.asession() as session:
            await session.arun_turn(boom)

    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.PROMPT_REJECTION
    assert terminal.detail == "RuntimeError raised"
    assert "content filter" not in str(terminal.to_dict())


# -- arun_turn with async circuit breaker --


@pytest.mark.asyncio
async def test_arun_turn_with_async_circuit_breaker():
    cb = AsyncCircuitBreaker("svc", failure_threshold=1, recovery_timeout_seconds=10)
    orch = AgentSession(role="writer", phase="draft", async_circuit_breaker=cb)

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
    orch = AgentSession(role="writer", phase="draft")

    async def slow():
        await asyncio.sleep(0.5)
        return "too late"

    with pytest.raises(TurnTimeoutError) as exc_info:
        async with orch.asession() as session:
            await session.arun_turn(slow, timeout=0.05)

    assert exc_info.value.timeout_seconds == 0.05
    assert any(event.scenario == "llm_timeout" for event in session.recovery.events)
    assert any(
        event.event.value == "agent.recovery.attempted"
        and event.detail == "llm_timeout: recovered"
        for event in session.events
    )
    assert any(
        event.event.value == "agent.recovery.succeeded"
        and event.data is not None
        and event.data["scenario"] == "llm_timeout"
        and event.data["outcome"] == "recovered"
        for event in session.events
    )


@pytest.mark.asyncio
async def test_arun_turn_no_timeout_completes():
    orch = AgentSession(role="writer", phase="draft")

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
    orch = AgentSession(role="reader", phase="draft", permissions=enforcer)

    async def attempt():
        return "leak"

    with pytest.raises(PermissionDeniedError):
        async with orch.asession() as session:
            await session.arun_tool("write_db", attempt)
    blocked = [e for e in session.events if e.event.value == "agent.blocked"]
    assert blocked[0].data == {"tool": "write_db", "kind": "permission"}
    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures[-1].failure_class == AgentFailureClass.PERMISSION_DENIED
    assert not any(e.event.value == "agent.tool_called" for e in session.events)


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
    orch = AgentSession(role="writer", phase="draft", permissions=enforcer)

    async def writer():
        return "wrote"

    async with orch.asession() as session:
        assert await session.arun_tool("write_db", writer) == "wrote"
    tool_events = [
        event for event in session.events if event.data == {"tool": "write_db"}
    ]
    assert [event.event.value for event in tool_events] == [
        "agent.tool_called",
        "agent.tool_completed",
    ]


@pytest.mark.asyncio
async def test_arun_tool_execution_error_emits_tool_failure_event_when_caught():
    orch = AgentSession(role="writer", phase="draft")

    async def fail_tool() -> str:
        raise RuntimeError("connection string with sensitive details")

    async with orch.asession() as session:
        with pytest.raises(RuntimeError):
            await session.arun_tool("lookup", fail_tool)

    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures[0].failure_class is not None
    assert failures[0].failure_class.value == "tool_error"
    assert failures[0].detail == "tool execution failed: lookup"
    assert failures[0].data == {"tool": "lookup"}
    assert "sensitive" not in str(failures[0].to_dict())
    assert any(e.event.value == "agent.tool_called" for e in session.events)
    assert not any(e.event.value == "agent.tool_completed" for e in session.events)


# -- Cancellation → CANCELLED (item 2.4) --


@pytest.mark.asyncio
async def test_cancelled_error_marks_worker_cancelled():
    orch = AgentSession(role="writer", phase="draft")

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
    failures = [
        event
        for event in run.session.events  # type: ignore[attr-defined]
        if event.event.value == "agent.failed"
    ]
    assert failures[-1].failure_class == AgentFailureClass.CANCELLED
    assert failures[-1].detail == "async session cancelled"


# -- Budget enforcement (parity with sync) --


@pytest.mark.asyncio
async def test_arun_turn_respects_enforce_budget():
    orch = AgentSession(
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
    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures
    assert all(e.failure_class is not None for e in failures)
    assert [e.failure_class.value for e in failures] == ["rate_limit", "rate_limit"]


# -- pause_for_input (item 2.8) --


@pytest.mark.asyncio
async def test_pause_for_input_resumes_with_provided_value():
    orch = AgentSession(role="writer", phase="draft")

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
    events = flow.session.events  # type: ignore[attr-defined]
    blocked = [event for event in events if event.event.value == "agent.blocked"]
    ready = [event for event in events if event.event.value == "agent.ready"]
    assert blocked[0].detail == "waiting for input"
    assert blocked[0].data == {"kind": "human_input"}
    assert ready[0].detail == "input received"
    assert ready[0].data == {"kind": "human_input"}
    public_payload = " ".join(str(event.to_dict()) for event in blocked + ready)
    assert "approve?" not in public_payload
    assert "approved" not in public_payload


@pytest.mark.asyncio
async def test_pause_for_input_rejects_double_pause():
    orch = AgentSession(role="writer", phase="draft")
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
    orch = AgentSession(
        role="writer",
        phase="draft",
        policy_engine=PolicyEngine([timed_out_rule]),
    )
    async with orch.asession() as session:
        await asyncio.sleep(0.02)  # ensure elapsed > 0
        actions = session.evaluate_policy()  # no elapsed_seconds — auto
        assert any(a.action == PolicyAction.ABORT_PHASE for a in actions)
