"""Tests for techrevati.runtime.orchestrator"""

import pytest

from techrevati.runtime import (
    AgentStatus,
    BudgetExceededError,
    CircuitBreaker,
    CircuitOpenError,
    ModelPricing,
    Orchestrator,
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
    orch = Orchestrator(role="writer", phase="draft", project_id=42)
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
    assert any(e.event.value == "agent.completed" for e in session.events)


def test_session_failure_path_marks_worker_failed():
    orch = Orchestrator(role="writer", phase="draft")
    with pytest.raises(RuntimeError):
        with orch.session() as session:
            session.run_turn(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert session.worker.status == AgentStatus.FAILED
    assert any("recovery" in e.event.value for e in session.events)


def test_permission_denied_blocks_run_tool():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)
            },
            tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
        )
    )
    orch = Orchestrator(role="reader", phase="draft", permissions=enforcer)
    with orch.session() as session:
        with pytest.raises(PermissionDeniedError):
            session.run_tool("expand_features", lambda: "should not run")


def test_run_tool_passes_when_allowed():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "writer": RolePermissionConfig("writer", PermissionMode.FULL_ACCESS)
            },
            tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
        )
    )
    orch = Orchestrator(role="writer", phase="draft", permissions=enforcer)
    with orch.session() as session:
        result = session.run_tool("expand_features", lambda: "result")
        assert result == "result"


def test_circuit_breaker_short_circuits_turn():
    cb = CircuitBreaker("svc", failure_threshold=1, recovery_timeout_seconds=10)
    orch = Orchestrator(role="writer", phase="draft", circuit_breaker=cb)

    with pytest.raises(RuntimeError):
        with orch.session() as session:
            session.run_turn(lambda: (_ for _ in ()).throw(RuntimeError("svc down")))

    with pytest.raises(CircuitOpenError):
        with orch.session() as session:
            session.run_turn(lambda: "should not run")


def test_evaluate_gate_records_event_on_pass():
    orch = Orchestrator(
        role="writer",
        phase="draft",
        quality_gate=QualityGate(QualityLevel.STANDARD),
    )
    with orch.session() as session:
        outcome = session.evaluate_gate(QualityLevel.STRICT)
        assert outcome.satisfied is True
        assert any(e.event.value == "phase.gate_passed" for e in session.events)


def test_evaluate_gate_records_event_on_fail():
    orch = Orchestrator(
        role="writer",
        phase="draft",
        quality_gate=QualityGate(QualityLevel.STRICT),
    )
    with orch.session() as session:
        outcome = session.evaluate_gate(QualityLevel.MINIMAL)
        assert outcome.satisfied is False
        assert any(e.event.value == "phase.gate_failed" for e in session.events)


def test_evaluate_gate_without_configured_gate_returns_none():
    orch = Orchestrator(role="writer", phase="draft")
    with orch.session() as session:
        assert session.evaluate_gate(QualityLevel.STRICT) is None


def test_evaluate_policy_returns_actions():
    rule = PolicyRule(
        name="advance-on-quality",
        condition=And([PhaseCompleted(), QualityAt(QualityLevel.STANDARD)]),
        actions=[PolicyActionData(PolicyAction.ADVANCE_PHASE)],
        priority=10,
    )
    orch = Orchestrator(
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
    orch = Orchestrator(role="writer", phase="draft")
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
    orch = Orchestrator(
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


def test_enforce_budget_default_is_informational_only():
    """Without enforce_budget=True, over-budget logs an event but doesn't raise."""
    orch = Orchestrator(
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
    assert any("budget exceeded" in (e.detail or "") for e in session.events)


def test_enforce_budget_does_not_fire_when_under_budget():
    orch = Orchestrator(
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
