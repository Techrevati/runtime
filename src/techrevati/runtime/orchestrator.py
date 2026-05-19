"""
Orchestrator — single execution loop wiring the runtime primitives together.

Pairs an agent session with lifecycle tracking, usage accounting,
circuit-breaker protection, automatic failure classification, permission
gating, and policy evaluation. Use the primitives standalone or use
`Orchestrator.session()` to get all of them wired in.

Example:
    from techrevati.runtime import Orchestrator, UsageSnapshot

    orch = Orchestrator(role="writer", phase="draft", project_id=1)
    with orch.session() as session:
        text, usage = session.run_turn(
            lambda: call_model(prompt),
            model="model-a",
            usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
        )
    print(session.summary())
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from techrevati.runtime.agent_events import AgentEvent, AgentFailureClass
from techrevati.runtime.agent_lifecycle import (
    AgentRegistry,
    AgentStatus,
    AgentWorker,
)
from techrevati.runtime.circuit_breaker import CircuitBreaker, CircuitOpenError
from techrevati.runtime.permissions import PermissionEnforcer, PermissionOutcome
from techrevati.runtime.policy_engine import (
    PhaseContext,
    PolicyActionData,
    PolicyEngine,
)
from techrevati.runtime.quality_gate import (
    QualityGate,
    QualityGateOutcome,
    QualityLevel,
)
from techrevati.runtime.retry_policy import (
    FailureScenario,
    RecoveryContext,
    attempt_recovery,
    classify_exception,
)
from techrevati.runtime.usage_tracking import UsageSnapshot, UsageTracker

T = TypeVar("T")


class PermissionDeniedError(Exception):
    """Raised when a tool is blocked by the configured PermissionEnforcer."""

    def __init__(self, outcome: PermissionOutcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.reason or "permission denied")


@dataclass
class Orchestrator:
    """Factory for sessions. Holds shared, long-lived components.

    Components are optional; the simplest invocation is
    `Orchestrator(role=..., phase=...)`.
    """

    role: str
    phase: str
    project_id: int | None = None
    registry: AgentRegistry = field(default_factory=AgentRegistry)
    permissions: PermissionEnforcer | None = None
    circuit_breaker: CircuitBreaker | None = None
    policy_engine: PolicyEngine | None = None
    quality_gate: QualityGate | None = None
    budget_usd: float | None = None

    @contextmanager
    def session(self) -> Iterator[OrchestrationSession]:
        """Open a single-agent session with its own worker + usage tracker.

        On clean exit, transitions the worker to COMPLETED if still
        running. On exception, transitions to FAILED with the error
        classified into an AgentFailureClass.
        """
        worker = self.registry.create(
            role=self.role, phase=self.phase, project_id=self.project_id
        )
        worker.transition(AgentStatus.INITIALIZING)
        worker.transition(AgentStatus.RUNNING)

        session = OrchestrationSession(
            worker=worker,
            tracker=UsageTracker(),
            recovery=RecoveryContext(),
            registry=self.registry,
            permissions=self.permissions,
            circuit_breaker=self.circuit_breaker,
            policy_engine=self.policy_engine,
            quality_gate=self.quality_gate,
            budget_usd=self.budget_usd,
            phase=self.phase,
            role=self.role,
            project_id=self.project_id,
        )

        try:
            yield session
        except Exception as exc:
            scenario = classify_exception(exc)
            session.fail(detail=str(exc), failure_class=_scenario_to_class(scenario))
            raise
        else:
            if not worker.is_terminal:
                session.complete()


@dataclass
class OrchestrationSession:
    """Single-agent execution context. Created by Orchestrator.session()."""

    worker: AgentWorker
    tracker: UsageTracker
    recovery: RecoveryContext
    registry: AgentRegistry
    phase: str
    role: str
    project_id: int | None
    permissions: PermissionEnforcer | None = None
    circuit_breaker: CircuitBreaker | None = None
    policy_engine: PolicyEngine | None = None
    quality_gate: QualityGate | None = None
    budget_usd: float | None = None
    events: list[AgentEvent] = field(default_factory=list)

    # -- Tool authorization --

    def authorize(self, tool_name: str) -> PermissionOutcome:
        """Check whether this session's role may use a tool."""
        if self.permissions is None:
            return PermissionOutcome(allowed=True, reason="no enforcer configured")
        return self.permissions.check(self.role, tool_name)

    def run_tool(
        self,
        tool_name: str,
        fn: Callable[[], T],
    ) -> T:
        """Execute a tool with a permission check around it.

        Raises PermissionDeniedError when blocked.
        """
        outcome = self.authorize(tool_name)
        if not outcome.allowed:
            raise PermissionDeniedError(outcome)
        return fn()

    # -- One execution turn with recovery + circuit breaker --

    def run_turn(
        self,
        fn: Callable[[], T],
        model: str = "",
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[T], UsageSnapshot] | None = None,
    ) -> tuple[T, UsageSnapshot]:
        """Execute one model turn with circuit-breaker + recovery wiring.

        On exception: classifies the failure, attempts recovery once,
        and re-raises the original exception so the caller decides
        whether to retry. The recovery decision is recorded in
        `self.recovery.events` and `self.events`.

        Returns (result, usage). `usage` is whichever was provided
        (or estimated) — recorded via `tracker.record_turn(model, usage)`.
        """
        try:
            if self.circuit_breaker is not None:
                result = self.circuit_breaker.call(fn)
            else:
                result = fn()
        except CircuitOpenError:
            raise
        except Exception as exc:
            scenario = classify_exception(exc)
            recovery_result = attempt_recovery(scenario, self.recovery)
            self.events.append(
                AgentEvent.recovery_attempted(
                    self.role,
                    self.phase,
                    detail=f"{scenario.value}: {recovery_result.outcome}",
                )
            )
            raise

        if usage is not None:
            snapshot = usage
        elif estimate_usage is not None:
            snapshot = estimate_usage(result)
        else:
            snapshot = UsageSnapshot()
        if model:
            self.tracker.record_turn(model, snapshot)

        budget = self.budget_usd
        if budget is not None and self.tracker.is_over_budget(budget):
            self.events.append(
                AgentEvent.failed(
                    self.role,
                    self.phase,
                    AgentFailureClass.UNKNOWN,
                    detail=f"budget exceeded: {self.tracker.format_cost()}",
                ).with_data({"budget_usd": budget})
            )

        return result, snapshot

    # -- Policy evaluation --

    def evaluate_policy(
        self,
        gate_score: float = 0.0,
        gate_threshold: float = 0.0,
        completed_roles: set[str] | None = None,
        failed_roles: set[str] | None = None,
        all_roles: set[str] | None = None,
        elapsed_seconds: float = 0.0,
        phase_completed: bool = False,
        quality_level: QualityLevel | None = None,
    ) -> list[PolicyActionData]:
        """Evaluate the configured policy engine against current state."""
        if self.policy_engine is None:
            return []

        ctx = PhaseContext(
            phase=self.phase,
            quality_level=quality_level,
            gate_score=gate_score,
            gate_threshold=gate_threshold,
            completed_roles=completed_roles or set(),
            failed_roles=failed_roles or set(),
            all_roles=all_roles or set(),
            elapsed_seconds=elapsed_seconds,
            retry_exhausted_scenarios={
                s.value
                for s in FailureScenario
                if any(
                    e.event_type == "escalated" and e.scenario == s.value
                    for e in self.recovery.events
                )
            },
            phase_completed=phase_completed,
            total_cost_usd=self.tracker.total_cost(),
        )
        return self.policy_engine.evaluate(ctx)

    # -- Quality gate --

    def evaluate_gate(self, observed: QualityLevel) -> QualityGateOutcome | None:
        """Evaluate the configured QualityGate against observed level.

        Returns the outcome and records a corresponding AgentEvent.
        Returns None if no quality_gate was configured.
        """
        if self.quality_gate is None:
            return None
        outcome = self.quality_gate.evaluate(observed)
        detail = f"observed={observed.name}"
        if outcome.satisfied:
            self.events.append(AgentEvent.gate_passed(self.phase, detail=detail))
        else:
            self.events.append(AgentEvent.gate_failed(self.phase, detail=detail))
        return outcome

    # -- Lifecycle --

    def complete(self, detail: str | None = None) -> None:
        if not self.worker.is_terminal:
            self.worker.transition(AgentStatus.COMPLETED, detail=detail)
        self.events.append(AgentEvent.completed(self.role, self.phase, detail=detail))

    def fail(
        self,
        detail: str,
        failure_class: AgentFailureClass = AgentFailureClass.UNKNOWN,
    ) -> None:
        if not self.worker.is_terminal:
            self.worker.transition(AgentStatus.FAILED, detail=detail)
        self.events.append(
            AgentEvent.failed(self.role, self.phase, failure_class, detail=detail)
        )

    # -- Summary --

    def summary(self) -> dict[str, Any]:
        """Aggregate snapshot of the session."""
        return {
            "worker": self.worker.to_dict(),
            "usage": self.tracker.summary(),
            "per_model_cost": self.tracker.per_model_summary(),
            "recovery_events": [e.to_dict() for e in self.recovery.events],
            "agent_events": [e.to_dict() for e in self.events],
        }


def _scenario_to_class(scenario: FailureScenario) -> AgentFailureClass:
    """Map a recovery FailureScenario to the event-level taxonomy."""
    mapping: dict[FailureScenario, AgentFailureClass] = {
        FailureScenario.LLM_TIMEOUT: AgentFailureClass.LLM_TIMEOUT,
        FailureScenario.LLM_ERROR: AgentFailureClass.LLM_ERROR,
        FailureScenario.TOOL_EXECUTION_ERROR: AgentFailureClass.TOOL_ERROR,
        FailureScenario.CONTEXT_OVERFLOW: AgentFailureClass.CONTEXT_OVERFLOW,
        FailureScenario.DEPENDENCY_TIMEOUT: AgentFailureClass.DEPENDENCY_FAILED,
        FailureScenario.MEMORY_CORRUPTION: AgentFailureClass.MEMORY_CORRUPTION,
        FailureScenario.PROVIDER_FAILURE: AgentFailureClass.DEPENDENCY_FAILED,
    }
    return mapping.get(scenario, AgentFailureClass.UNKNOWN)


__all__ = [
    "Orchestrator",
    "OrchestrationSession",
    "PermissionDeniedError",
]
