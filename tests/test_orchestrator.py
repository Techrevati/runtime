"""Tests for techrevati.runtime.orchestrator"""

import time
from typing import Any, cast

import pytest

from techrevati.runtime import (
    AgentFailureClass,
    AgentSession,
    AgentStatus,
    BudgetExceededError,
    CircuitBreaker,
    CircuitOpenError,
    ModelPricing,
    PermissionDeniedError,
    PermissionEnforcer,
    PermissionMode,
    PermissionPolicy,
    PolicyAction,
    PolicyActionData,
    PolicyEngine,
    PolicyRule,
    QualityGate,
    QualityLevel,
    RolePermissionConfig,
    TurnTimeoutError,
    UsageSnapshot,
    register_pricing,
)
from techrevati.runtime.policy_engine import And, PhaseCompleted, QualityAt


@pytest.fixture(autouse=True)
def _model_pricing_for_cost_assertions():
    """Register a known-priced model so cost assertions are not flaky."""
    register_pricing(
        "test-model", ModelPricing(input_per_million=3.0, output_per_million=15.0)
    )


def test_session_happy_path_completes_worker():
    orch = AgentSession(role="writer", phase="draft", project_id=42)
    with orch.session() as session:
        result, usage = session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=1000, output_tokens=500),
        )
        assert result == "ok"
        assert usage.input_tokens == 1000

    assert session.worker.status == AgentStatus.COMPLETED
    assert session.tracker.total_cost() > 0
    assert session.events[0].event.value == "agent.started"
    assert session.events[0].project_id == 42
    assert any(e.event.value == "agent.completed" for e in session.events)


def test_cancel_marks_terminal_failure_class_cancelled():
    orch = AgentSession(role="writer", phase="draft")

    with orch.session() as session:
        session.cancel(detail="user requested cancellation")

    failures = [
        event for event in session.events if event.event.value == "agent.failed"
    ]
    assert session.worker.status == AgentStatus.CANCELLED
    assert failures[-1].failure_class == AgentFailureClass.CANCELLED
    assert failures[-1].detail == "user requested cancellation"
    assert not any(event.event.value == "agent.completed" for event in session.events)


def test_agent_session_rejects_invalid_config():
    with pytest.raises(ValueError, match="role"):
        AgentSession(role="", phase="draft")
    with pytest.raises(TypeError, match="role"):
        AgentSession(role=cast(Any, 123), phase="draft")
    with pytest.raises(ValueError, match="phase"):
        AgentSession(role="writer", phase=" ")
    with pytest.raises(TypeError, match="project_id"):
        AgentSession(role="writer", phase="draft", project_id=cast(Any, True))
    with pytest.raises(ValueError, match="project_id"):
        AgentSession(role="writer", phase="draft", project_id=-1)
    with pytest.raises(TypeError, match="budget_usd"):
        AgentSession(role="writer", phase="draft", budget_usd=cast(Any, False))
    with pytest.raises(ValueError, match="budget_usd"):
        AgentSession(role="writer", phase="draft", budget_usd=float("nan"))
    with pytest.raises(ValueError, match="budget_usd"):
        AgentSession(role="writer", phase="draft", budget_usd=-0.01)
    with pytest.raises(TypeError, match="enforce_budget"):
        AgentSession(role="writer", phase="draft", enforce_budget=cast(Any, "yes"))
    with pytest.raises(TypeError, match="max_iterations"):
        AgentSession(role="writer", phase="draft", max_iterations=cast(Any, True))
    with pytest.raises(ValueError, match="max_iterations"):
        AgentSession(role="writer", phase="draft", max_iterations=-1)


def test_session_rejects_empty_thread_id_before_starting_worker():
    orch = AgentSession(role="writer", phase="draft")
    with pytest.raises(ValueError, match="thread_id"):
        with orch.session(thread_id=""):
            pass
    assert orch.registry.list_active() == []


def test_session_failure_path_marks_worker_failed():
    orch = AgentSession(role="writer", phase="draft")
    with pytest.raises(RuntimeError, match="sensitive details"):
        with orch.session() as session:
            session.run_turn(
                lambda: (_ for _ in ()).throw(
                    RuntimeError("connection string with sensitive details")
                )
            )

    assert session.worker.status == AgentStatus.FAILED
    assert any("recovery" in e.event.value for e in session.events)
    assert any(e.event.value == "agent.recovery.succeeded" for e in session.events)
    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.LLM_ERROR
    assert terminal.detail == "RuntimeError raised"
    assert "sensitive" not in str(terminal.to_dict())
    assert "sensitive" not in str(session.worker.to_dict())


def test_validation_error_marks_terminal_failure_class():
    orch = AgentSession(role="writer", phase="draft")

    with pytest.raises(ValueError, match="invalid payload"):
        with orch.session() as session:
            session.run_turn(
                lambda: (_ for _ in ()).throw(ValueError("invalid payload"))
            )

    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.VALIDATION_ERROR
    assert terminal.detail == "ValueError raised"
    assert "invalid payload" not in str(terminal.to_dict())


def test_prompt_rejection_marks_terminal_failure_class():
    orch = AgentSession(role="writer", phase="draft")

    with pytest.raises(RuntimeError, match="prompt rejected"):
        with orch.session() as session:
            session.run_turn(
                lambda: (_ for _ in ()).throw(
                    RuntimeError("prompt rejected by content policy")
                )
            )

    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.PROMPT_REJECTION
    assert terminal.detail == "RuntimeError raised"
    assert "content policy" not in str(terminal.to_dict())


def test_prompt_rejection_takes_priority_over_validation_fallback():
    orch = AgentSession(role="writer", phase="draft")

    with pytest.raises(ValueError, match="safety policy"):
        with orch.session() as session:
            session.run_turn(lambda: (_ for _ in ()).throw(ValueError("safety policy")))

    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.PROMPT_REJECTION


def test_context_overflow_validation_message_keeps_specific_failure_class():
    orch = AgentSession(role="writer", phase="draft")

    with pytest.raises(ValueError, match="context_length_exceeded"):
        with orch.session() as session:
            session.run_turn(
                lambda: (_ for _ in ()).throw(ValueError("context_length_exceeded"))
            )

    terminal = session.events[-1]
    assert terminal.failure_class == AgentFailureClass.CONTEXT_OVERFLOW


def test_permission_denied_blocks_run_tool():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)
            },
            tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
        )
    )
    orch = AgentSession(role="reader", phase="draft", permissions=enforcer)
    with orch.session() as session:
        with pytest.raises(PermissionDeniedError):
            session.run_tool("expand_features", lambda: "should not run")
    blocked = [e for e in session.events if e.event.value == "agent.blocked"]
    assert blocked[0].data == {"tool": "expand_features", "kind": "permission"}
    assert not any(e.event.value == "agent.tool_called" for e in session.events)


def test_uncaught_permission_denied_marks_terminal_failure_class():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)
            },
            tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
        )
    )
    orch = AgentSession(role="reader", phase="draft", permissions=enforcer)

    with pytest.raises(PermissionDeniedError):
        with orch.session() as session:
            session.run_tool("expand_features", lambda: "should not run")

    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures[-1].failure_class == AgentFailureClass.PERMISSION_DENIED


def test_run_tool_passes_when_allowed():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "writer": RolePermissionConfig("writer", PermissionMode.FULL_ACCESS)
            },
            tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
        )
    )
    orch = AgentSession(role="writer", phase="draft", permissions=enforcer)
    with orch.session() as session:
        result = session.run_tool("expand_features", lambda: "result")
        assert result == "result"
    tool_events = [
        event for event in session.events if event.data == {"tool": "expand_features"}
    ]
    assert [event.event.value for event in tool_events] == [
        "agent.tool_called",
        "agent.tool_completed",
    ]


def test_run_tool_execution_error_emits_tool_failure_event_when_caught():
    orch = AgentSession(role="writer", phase="draft")

    def fail_tool() -> str:
        raise RuntimeError("connection string with sensitive details")

    with orch.session() as session:
        with pytest.raises(RuntimeError):
            session.run_tool("lookup", fail_tool)

    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures[0].failure_class is not None
    assert failures[0].failure_class.value == "tool_error"
    assert failures[0].detail == "tool execution failed: lookup"
    assert failures[0].data == {"tool": "lookup"}
    assert "sensitive" not in str(failures[0].to_dict())
    assert any(e.event.value == "agent.tool_called" for e in session.events)
    assert not any(e.event.value == "agent.tool_completed" for e in session.events)


def test_circuit_breaker_short_circuits_turn():
    cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout_seconds=10)
    orch = AgentSession(role="writer", phase="draft", circuit_breaker=cb)

    with pytest.raises(RuntimeError):
        with orch.session() as session:
            session.run_turn(lambda: (_ for _ in ()).throw(RuntimeError("svc down")))

    with pytest.raises(CircuitOpenError):
        with orch.session() as session:
            session.run_turn(lambda: "should not run")


def test_evaluate_gate_records_event_on_pass():
    orch = AgentSession(
        role="writer",
        phase="draft",
        quality_gate=QualityGate(QualityLevel.STANDARD),
    )
    with orch.session() as session:
        outcome = session.evaluate_gate(QualityLevel.STRICT)
        assert outcome.satisfied is True
        assert any(e.event.value == "phase.gate_passed" for e in session.events)


def test_evaluate_gate_records_event_on_fail():
    orch = AgentSession(
        role="writer",
        phase="draft",
        quality_gate=QualityGate(QualityLevel.STRICT),
    )
    with orch.session() as session:
        outcome = session.evaluate_gate(QualityLevel.MINIMAL)
        assert outcome.satisfied is False
        assert any(e.event.value == "phase.gate_failed" for e in session.events)


def test_evaluate_gate_without_configured_gate_returns_none():
    orch = AgentSession(role="writer", phase="draft")
    with orch.session() as session:
        assert session.evaluate_gate(QualityLevel.STRICT) is None


def test_evaluate_policy_returns_actions():
    rule = PolicyRule(
        name="advance-on-quality",
        condition=And([PhaseCompleted(), QualityAt(QualityLevel.STANDARD)]),
        actions=[PolicyActionData(PolicyAction.ADVANCE_PHASE)],
        priority=10,
    )
    orch = AgentSession(
        role="writer",
        phase="draft",
        policy_engine=PolicyEngine([rule]),
    )
    with orch.session() as session:
        actions = session.evaluate_policy(
            phase_completed=True,
            quality_level=QualityLevel.STRICT,
            completed_roles={"writer"},
            all_roles={"writer"},
        )
        assert PolicyAction.ADVANCE_PHASE.value in [a.action.value for a in actions]


def test_summary_includes_worker_and_usage():
    orch = AgentSession(role="writer", phase="draft")
    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=1000, output_tokens=500),
        )
    summary = session.summary()
    assert summary["worker"]["status"] == "completed"
    assert summary["usage"]["turns"] == 1
    assert "test-model" in summary["per_model_cost"]


def test_enforce_budget_raises_when_over():
    """enforce_budget=True converts over-budget into BudgetExceededError."""
    orch = AgentSession(
        role="writer",
        phase="draft",
        budget_usd=0.001,
        enforce_budget=True,
    )
    with pytest.raises(BudgetExceededError) as exc_info:
        with orch.session() as session:
            session.run_turn(
                lambda: "ok",
                model="test-model",
                usage=UsageSnapshot(input_tokens=1_000_000, output_tokens=0),
            )
    assert exc_info.value.budget_usd == 0.001
    assert exc_info.value.current_cost_usd > 0.001
    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures
    assert all(e.failure_class is not None for e in failures)
    assert [e.failure_class.value for e in failures] == ["rate_limit", "rate_limit"]


def test_enforce_budget_default_is_informational_only():
    """Without enforce_budget=True, over-budget logs an event but doesn't raise."""
    orch = AgentSession(
        role="writer",
        phase="draft",
        budget_usd=0.001,
    )
    with orch.session() as session:
        result, _ = session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=1_000_000, output_tokens=0),
        )
    assert result == "ok"
    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures[0].failure_class is not None
    assert failures[0].failure_class.value == "rate_limit"
    assert "budget exceeded" in (failures[0].detail or "")


def test_enforce_budget_does_not_fire_when_under_budget():
    orch = AgentSession(
        role="writer",
        phase="draft",
        budget_usd=100.0,
        enforce_budget=True,
    )
    with orch.session() as session:
        result, _ = session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=1000, output_tokens=100),
        )
    assert result == "ok"


# -- Sync timeout (Sprint 2.6) --


def test_run_turn_timeout_raises_turn_timeout_error():
    orch = AgentSession(role="writer", phase="draft")

    def slow():
        time.sleep(0.5)
        return "too late"

    with pytest.raises(TurnTimeoutError) as exc_info:
        with orch.session() as session:
            session.run_turn(slow, timeout=0.05)

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


def test_recovery_escalation_event_after_attempt_budget_exhausted():
    orch = AgentSession(role="writer", phase="draft")

    def timed_out() -> str:
        raise TimeoutError("model call timed out")

    with orch.session() as session:
        for _ in range(3):
            with pytest.raises(TimeoutError):
                session.run_turn(timed_out)

    escalated = [
        event
        for event in session.events
        if event.event.value == "agent.recovery.escalated"
    ]
    assert len(escalated) == 1
    assert escalated[0].detail == "llm_timeout: escalation_required"
    assert escalated[0].data is not None
    assert escalated[0].data["scenario"] == "llm_timeout"
    assert escalated[0].data["outcome"] == "escalation_required"


def test_run_turn_no_timeout_completes():
    orch = AgentSession(role="writer", phase="draft")
    with orch.session() as session:
        result, _ = session.run_turn(lambda: "fast", timeout=1.0)
    assert result == "fast"


# -- Auto-wired elapsed (Sprint 2.7) --


def test_evaluate_policy_auto_elapsed_sync():
    from techrevati.runtime.policy_engine import TimedOut

    rule = PolicyRule(
        name="hit-deadline",
        condition=TimedOut(seconds=0.0),
        actions=[PolicyActionData(PolicyAction.ABORT_PHASE)],
        priority=10,
    )
    orch = AgentSession(
        role="writer", phase="draft", policy_engine=PolicyEngine([rule])
    )
    with orch.session() as session:
        time.sleep(0.02)
        actions = session.evaluate_policy()  # no explicit elapsed
        assert any(a.action == PolicyAction.ABORT_PHASE for a in actions)


def test_evaluate_policy_explicit_elapsed_overrides_auto():
    from techrevati.runtime.policy_engine import TimedOut

    rule = PolicyRule(
        name="hit-deadline",
        condition=TimedOut(seconds=100.0),
        actions=[PolicyActionData(PolicyAction.ABORT_PHASE)],
        priority=10,
    )
    orch = AgentSession(
        role="writer", phase="draft", policy_engine=PolicyEngine([rule])
    )
    with orch.session() as session:
        actions = session.evaluate_policy(elapsed_seconds=200.0)
        assert any(a.action == PolicyAction.ABORT_PHASE for a in actions)
        # Auto-elapsed alone would be <<100s; explicit value made it fire.
