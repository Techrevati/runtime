"""
Orchestrator — single execution loop wiring the runtime primitives together.

Pairs an agent session with lifecycle tracking, usage accounting,
circuit-breaker protection, automatic failure classification, permission
gating, and policy evaluation. Use the primitives standalone or use
``Orchestrator.session()`` (sync) / ``Orchestrator.asession()`` (async)
to get all of them wired in.

Example (sync):
    from techrevati.runtime import Orchestrator, UsageSnapshot

    orch = Orchestrator(role="writer", phase="draft", project_id=1)
    with orch.session() as session:
        text, usage = session.run_turn(
            lambda: call_model(prompt),
            model="model-a",
            usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
        )
    print(session.summary())

Example (async):
    async with orch.asession() as session:
        text, usage = await session.arun_turn(
            lambda: acall_model(prompt),
            model="model-a",
            timeout=30.0,
        )
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from techrevati.runtime.agent_events import AgentEvent, AgentFailureClass
from techrevati.runtime.agent_lifecycle import (
    AgentRegistry,
    AgentStatus,
    AgentWorker,
)
from techrevati.runtime.checkpoint import CheckpointSaver
from techrevati.runtime.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitBreaker,
    CircuitOpenError,
)
from techrevati.runtime.governance import GovernanceBreachError, GovernancePlane
from techrevati.runtime.guardrails import (
    AsyncGuardrail,
    Guardrail,
    arun_post_checks,
    arun_pre_checks,
    run_post_checks,
    run_pre_checks,
)
from techrevati.runtime.handoffs import Handoff
from techrevati.runtime.hooks import (
    HookContext,
    HookLike,
    arun_after_model,
    arun_after_tool,
    arun_before_model,
    arun_before_tool,
    run_after_model,
    run_after_tool,
    run_before_model,
    run_before_tool,
)
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
from techrevati.runtime.rate_limit import AsyncRateLimiter, RateLimiter
from techrevati.runtime.retry_policy import (
    FailureScenario,
    RecoveryContext,
    aattempt_recovery,
    attempt_recovery,
    classify_exception,
)
from techrevati.runtime.routing import ProviderRouter
from techrevati.runtime.sinks import (
    EventSink,
    NoopEventSink,
    NoopUsageSink,
    UsageSink,
)
from techrevati.runtime.streaming import StreamEvent
from techrevati.runtime.usage_tracking import (
    BudgetExceededError,
    UsageLimits,
    UsageSnapshot,
    UsageTracker,
)

logger = logging.getLogger("techrevati.runtime.orchestrator")
logger.addHandler(logging.NullHandler())

T = TypeVar("T")


class PermissionDeniedError(Exception):
    """Raised when a tool is blocked by the configured PermissionEnforcer."""

    def __init__(self, outcome: PermissionOutcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.reason or "permission denied")


class TurnTimeoutError(Exception):
    """Raised when a sync or async turn exceeds the configured timeout.

    For sync callers, this wraps the underlying ``concurrent.futures.TimeoutError``;
    for async callers, the original ``asyncio.TimeoutError`` is re-raised
    as this type to give a single error class across both code paths.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"turn exceeded timeout of {timeout_seconds:.3f}s")


class MaxIterationsExceededError(Exception):
    """Raised when a session attempts more turns than ``max_iterations`` allows.

    Default cap of 25 matches the OpenAI Agents SDK convention and prevents
    runaway agent loops — stopping conditions are an industry
    production-readiness requirement.
    """

    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations
        super().__init__(
            f"session reached max_iterations={max_iterations}; "
            "raise the cap or shorten the loop"
        )


def _record_recovery_event(session: OrchestrationSession, exc: Exception) -> None:
    """Classify exc and emit a recovery-attempted event on the session.

    Shared between sync and async run_turn so behavior stays in lock-step.
    """
    scenario = classify_exception(exc)
    recovery_result = attempt_recovery(scenario, session.recovery)
    session._emit_event(
        AgentEvent.recovery_attempted(
            session.role,
            session.phase,
            detail=f"{scenario.value}: {recovery_result.outcome}",
        )
    )
    logger.info(
        "recovery_attempted",
        extra={
            "role": session.role,
            "phase": session.phase,
            "project_id": session.project_id,
            "scenario": scenario.value,
            "outcome": recovery_result.outcome,
        },
    )


async def _arecord_recovery_event(
    session: AsyncOrchestrationSession, exc: Exception
) -> None:
    """Async sibling of _record_recovery_event."""
    scenario = classify_exception(exc)
    recovery_result = await aattempt_recovery(scenario, session.recovery)
    session._emit_event(
        AgentEvent.recovery_attempted(
            session.role,
            session.phase,
            detail=f"{scenario.value}: {recovery_result.outcome}",
        )
    )
    logger.info(
        "recovery_attempted",
        extra={
            "role": session.role,
            "phase": session.phase,
            "project_id": session.project_id,
            "scenario": scenario.value,
            "outcome": recovery_result.outcome,
        },
    )


@dataclass
class AgentSession:
    """Factory for sessions. Holds shared, long-lived components.

    Components are optional; the simplest invocation is
    ``Orchestrator(role=..., phase=...)``. Provide ``circuit_breaker``
    for sync sessions, ``async_circuit_breaker`` for async sessions, or
    both — they are independent.

    To make sessions restart-resumable, pass ``saver`` (any object that
    satisfies the ``CheckpointSaver`` protocol) and a ``thread_id`` at
    ``session()`` / ``asession()`` time. The thread id is the durable
    handle a future process uses to pick up where this one left off.
    """

    role: str
    phase: str
    project_id: int | None = None
    registry: AgentRegistry = field(default_factory=AgentRegistry)
    permissions: PermissionEnforcer | None = None
    circuit_breaker: CircuitBreaker | None = None
    async_circuit_breaker: AsyncCircuitBreaker | None = None
    policy_engine: PolicyEngine | None = None
    quality_gate: QualityGate | None = None
    budget_usd: float | None = None
    enforce_budget: bool = False
    max_iterations: int = 25
    guardrails: list[Guardrail | AsyncGuardrail] = field(default_factory=list)
    event_sink: EventSink = field(default_factory=NoopEventSink)
    usage_sink: UsageSink = field(default_factory=NoopUsageSink)
    saver: CheckpointSaver | None = None
    rate_limiter: RateLimiter | None = None
    async_rate_limiter: AsyncRateLimiter | None = None
    provider_router: ProviderRouter | None = None
    usage_limits: UsageLimits | None = None
    governance: GovernancePlane | None = None
    hooks: list[HookLike] = field(default_factory=list)

    @contextmanager
    def session(
        self, *, thread_id: str | None = None
    ) -> Iterator[OrchestrationSession]:
        """Open a single-agent sync session.

        On clean exit: worker → COMPLETED if still running. On exception:
        worker → FAILED with the error classified into an AgentFailureClass.

        If ``thread_id`` is supplied and the orchestrator has a ``saver``,
        the session writes a checkpoint after each turn and an
        ``idempotency_key`` on ``run_turn`` makes that turn replay-safe.
        """
        worker = self._start_worker()
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
            enforce_budget=self.enforce_budget,
            max_iterations=self.max_iterations,
            guardrails=list(self.guardrails),
            event_sink=self.event_sink,
            usage_sink=self.usage_sink,
            phase=self.phase,
            role=self.role,
            project_id=self.project_id,
            thread_id=thread_id,
            saver=self.saver,
            provider_router=self.provider_router,
            rate_limiter=self.rate_limiter,
            usage_limits=self.usage_limits,
            governance=self.governance,
            hooks=list(self.hooks),
        )

        try:
            yield session
        except GovernanceBreachError:
            # Terminal — record FAILED with governance class, do NOT recover.
            session.fail(
                detail="governance breach",
                failure_class=AgentFailureClass.DEPENDENCY_FAILED,
            )
            raise
        except Exception as exc:
            scenario = classify_exception(exc)
            session.fail(detail=str(exc), failure_class=_scenario_to_class(scenario))
            raise
        else:
            if not worker.is_terminal:
                session.complete()

    @asynccontextmanager
    async def asession(
        self, *, thread_id: str | None = None
    ) -> AsyncIterator[AsyncOrchestrationSession]:
        """Open a single-agent async session.

        Mirrors ``session()`` but uses async primitives. ``CancelledError``
        from anywhere inside the ``async with`` body transitions the
        worker to CANCELLED instead of FAILED, and is re-raised.

        The same ``thread_id`` / ``saver`` contract from ``session()``
        applies; pair with ``arun_turn(..., idempotency_key=...)`` for
        replay-safe async turns.
        """
        worker = self._start_worker()
        session = AsyncOrchestrationSession(
            worker=worker,
            tracker=UsageTracker(),
            recovery=RecoveryContext(),
            registry=self.registry,
            permissions=self.permissions,
            circuit_breaker=self.async_circuit_breaker,
            policy_engine=self.policy_engine,
            quality_gate=self.quality_gate,
            budget_usd=self.budget_usd,
            enforce_budget=self.enforce_budget,
            max_iterations=self.max_iterations,
            guardrails=list(self.guardrails),
            event_sink=self.event_sink,
            usage_sink=self.usage_sink,
            phase=self.phase,
            role=self.role,
            project_id=self.project_id,
            thread_id=thread_id,
            saver=self.saver,
            provider_router=self.provider_router,
            async_rate_limiter=self.async_rate_limiter,
            usage_limits=self.usage_limits,
            governance=self.governance,
            hooks=list(self.hooks),
        )

        try:
            yield session
        except asyncio.CancelledError:
            session.cancel(detail="async session cancelled")
            raise
        except GovernanceBreachError:
            session.fail(
                detail="governance breach",
                failure_class=AgentFailureClass.DEPENDENCY_FAILED,
            )
            raise
        except Exception as exc:
            scenario = classify_exception(exc)
            session.fail(detail=str(exc), failure_class=_scenario_to_class(scenario))
            raise
        else:
            if not worker.is_terminal:
                session.complete()

    def _start_worker(self) -> AgentWorker:
        """Create a worker and walk it from IDLE to RUNNING."""
        worker = self.registry.create(
            role=self.role, phase=self.phase, project_id=self.project_id
        )
        worker.transition(AgentStatus.INITIALIZING)
        worker.transition(AgentStatus.RUNNING)
        return worker


@dataclass
class _SessionBase:
    """Fields and helpers shared between sync and async sessions."""

    worker: AgentWorker
    tracker: UsageTracker
    recovery: RecoveryContext
    registry: AgentRegistry
    phase: str
    role: str
    project_id: int | None
    permissions: PermissionEnforcer | None = None
    policy_engine: PolicyEngine | None = None
    quality_gate: QualityGate | None = None
    budget_usd: float | None = None
    enforce_budget: bool = False
    max_iterations: int = 25
    guardrails: list[Guardrail | AsyncGuardrail] = field(default_factory=list)
    event_sink: EventSink = field(default_factory=NoopEventSink)
    usage_sink: UsageSink = field(default_factory=NoopUsageSink)
    events: list[AgentEvent] = field(default_factory=list)
    thread_id: str | None = None
    saver: CheckpointSaver | None = None
    provider_router: ProviderRouter | None = None
    usage_limits: UsageLimits | None = None
    governance: GovernancePlane | None = None
    hooks: list[HookLike] = field(default_factory=list)
    _started_at: float = field(default_factory=time.monotonic, init=False, repr=False)
    _iteration_count: int = field(default=0, init=False, repr=False)
    _last_stream_cancelled: bool = field(default=False, init=False, repr=False)

    def _resolve_hook_ctx(
        self,
        ctx: HookContext | None,
        *,
        model: str = "",
        prompt: Any = None,
        tool: str = "",
        args: dict[str, Any] | None = None,
    ) -> HookContext:
        """Return a HookContext for the current call, synthesizing one if absent.

        Mutates the supplied ``ctx`` (when provided) to attach the role / phase /
        model / tool defaults, so hooks always see them even when the caller
        passed a partially-populated context.
        """
        if ctx is None:
            return HookContext(
                role=self.role,
                phase=self.phase,
                model=model,
                prompt=prompt,
                tool=tool,
                args=args or {},
            )
        ctx.role = self.role
        ctx.phase = self.phase
        if model:
            ctx.model = model
        if prompt is not None and ctx.prompt is None:
            ctx.prompt = prompt
        if tool:
            ctx.tool = tool
        if args is not None and not ctx.args:
            ctx.args = args
        return ctx

    # -- Durable execution helpers (idempotent replay + per-turn checkpoint).
    # Both no-op gracefully when either ``thread_id`` or ``saver`` is absent,
    # so the call sites can stay flat.

    def _restore_idempotent_turn(
        self, idempotency_key: str | None
    ) -> tuple[Any, UsageSnapshot] | None:
        """Return a cached (result, usage) if this idempotency_key already ran."""
        if not (idempotency_key and self.thread_id and self.saver is not None):
            return None
        # Bounded scan over the most recent checkpoints. 100 is plenty for a
        # typical session; callers expecting truly long-lived threads should
        # cache the lookup outside the runtime.
        for cp in self.saver.list(self.thread_id, limit=100):
            if cp.metadata.get("idempotency_key") == idempotency_key:
                result = cp.state.get("result")
                usage_data = cp.state.get("usage") or {}
                return result, UsageSnapshot.from_dict(usage_data)
        return None

    def _persist_turn_checkpoint(
        self,
        *,
        result: Any,
        usage: UsageSnapshot,
        model: str,
        idempotency_key: str | None,
    ) -> None:
        """Write a checkpoint for the turn. JSON-serializable results only.

        Non-serializable results are skipped with a logged warning rather
        than raising, so a session that does not care about durability
        keeps working when the saver is configured globally.
        """
        if not (self.thread_id and self.saver is not None):
            return
        try:
            result_payload = json.loads(json.dumps(result))
        except (TypeError, ValueError):
            logger.warning(
                "checkpoint skipped: result for model=%s is not "
                "JSON-serializable; pass a JSON-friendly value or omit "
                "idempotency_key",
                model or "<unspecified>",
            )
            return
        metadata: dict[str, Any] = {"status": "completed", "model": model}
        if idempotency_key:
            metadata["idempotency_key"] = idempotency_key
        self.saver.put(
            self.thread_id,
            state={"result": result_payload, "usage": usage.to_dict()},
            metadata=metadata,
        )

    # -- Tool authorization (sync; safe to call from async context too) --

    def authorize(self, tool_name: str) -> PermissionOutcome:
        """Check whether this session's role may use a tool."""
        if self.permissions is None:
            return PermissionOutcome(allowed=True, reason="no enforcer configured")
        return self.permissions.check(self.role, tool_name)

    # -- Policy evaluation --

    def evaluate_policy(
        self,
        gate_score: float = 0.0,
        gate_threshold: float = 0.0,
        completed_roles: set[str] | None = None,
        failed_roles: set[str] | None = None,
        all_roles: set[str] | None = None,
        elapsed_seconds: float | None = None,
        phase_completed: bool = False,
        quality_level: QualityLevel | None = None,
    ) -> list[PolicyActionData]:
        """Evaluate the configured policy engine against current state.

        If ``elapsed_seconds`` is not provided, it is auto-computed from
        when the session was created. This closes the TimedOut-never-fires
        gap from 0.0.x where callers had to track time themselves.
        """
        if self.policy_engine is None:
            return []

        effective_elapsed = (
            elapsed_seconds
            if elapsed_seconds is not None
            else time.monotonic() - self._started_at
        )

        ctx = PhaseContext(
            phase=self.phase,
            quality_level=quality_level,
            gate_score=gate_score,
            gate_threshold=gate_threshold,
            completed_roles=completed_roles or set(),
            failed_roles=failed_roles or set(),
            all_roles=all_roles or set(),
            elapsed_seconds=effective_elapsed,
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
        """Evaluate the configured QualityGate against observed level."""
        if self.quality_gate is None:
            return None
        outcome = self.quality_gate.evaluate(observed)
        detail = f"observed={observed.name}"
        if outcome.satisfied:
            self._emit_event(AgentEvent.gate_passed(self.phase, detail=detail))
        else:
            self._emit_event(AgentEvent.gate_failed(self.phase, detail=detail))
            logger.info(
                "quality_gate_failed",
                extra={
                    "role": self.role,
                    "phase": self.phase,
                    "project_id": self.project_id,
                    "observed": observed.name,
                },
            )
        return outcome

    # -- Lifecycle --

    def complete(self, detail: str | None = None) -> None:
        if not self.worker.is_terminal:
            self.worker.transition(AgentStatus.COMPLETED, detail=detail)
        self._emit_event(AgentEvent.completed(self.role, self.phase, detail=detail))

    def fail(
        self,
        detail: str,
        failure_class: AgentFailureClass = AgentFailureClass.UNKNOWN,
    ) -> None:
        if not self.worker.is_terminal:
            self.worker.transition(AgentStatus.FAILED, detail=detail)
        self._emit_event(
            AgentEvent.failed(self.role, self.phase, failure_class, detail=detail)
        )
        logger.info(
            "session_failed",
            extra={
                "role": self.role,
                "phase": self.phase,
                "project_id": self.project_id,
                "failure_class": failure_class.value,
                "detail": detail,
            },
        )

    def cancel(self, detail: str | None = None) -> None:
        """Mark the worker as cancelled (e.g. from asyncio.CancelledError)."""
        if not self.worker.is_terminal:
            self.worker.transition(AgentStatus.CANCELLED, detail=detail)
        self._emit_event(
            AgentEvent.failed(
                self.role,
                self.phase,
                AgentFailureClass.UNKNOWN,
                detail=detail or "cancelled",
            )
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

    # -- Internal helpers --

    def _resolve_usage(
        self,
        result: T,
        usage: UsageSnapshot | None,
        estimate_usage: Callable[[T], UsageSnapshot] | None,
    ) -> UsageSnapshot:
        if usage is not None:
            return usage
        if estimate_usage is not None:
            return estimate_usage(result)
        return UsageSnapshot()

    def _emit_event(self, event: AgentEvent) -> None:
        """Append to the session event log AND forward to the configured sink.

        Use this everywhere instead of ``self.events.append(event)`` so
        observability stays consistent. The sink call is wrapped in a
        try/except so a misbehaving sink can't break the session.
        """
        self.events.append(event)
        try:
            self.event_sink.emit(event)
        except Exception:
            logger.exception(
                "event_sink.emit raised; suppressing to keep session alive"
            )

    def _check_iteration_cap(self) -> None:
        """Raise MaxIterationsExceededError if this turn would exceed the cap.

        Called BEFORE the user's fn runs so the cap means "we will not make
        a (max+1)th call", not "we just made one too many".
        """
        if self._iteration_count >= self.max_iterations:
            raise MaxIterationsExceededError(self.max_iterations)
        self._iteration_count += 1

    def _emit_governance_outcomes(self, outcomes: list[Any]) -> None:
        """Emit governance.alert events for breached alert-mode limits."""
        for outcome in outcomes:
            if outcome.breached and outcome.on_breach == "alert":
                self._emit_event(
                    AgentEvent.governance_alert(
                        self.role,
                        self.phase,
                        limit_name=outcome.limit_name,
                        observed=outcome.observed,
                        ceiling=outcome.ceiling,
                        scope=outcome.scope,
                    )
                )

    def _enforce_governance(self) -> None:
        """Enforce + emit alerts + emit breach event right before raising."""
        if self.governance is None:
            return
        try:
            outcomes = self.governance.enforce()
        except GovernanceBreachError as err:
            self._emit_event(
                AgentEvent.governance_breach(
                    self.role,
                    self.phase,
                    limit_name=err.limit_name,
                    observed=err.observed,
                    ceiling=err.ceiling,
                    scope=err.scope,
                )
            )
            raise
        self._emit_governance_outcomes(outcomes)

    def _check_governance_pre_turn(self) -> None:
        """Tick the turn counter and enforce limits before fn runs."""
        if self.governance is None:
            return
        self.governance.state.record_turn_start()
        self._enforce_governance()

    def _check_governance_pre_tool(self) -> None:
        """Tick the tool counter and enforce limits before tool fn runs."""
        if self.governance is None:
            return
        self.governance.state.record_tool_call()
        self._enforce_governance()

    def _record_governance_turn_outcome(
        self, *, success: bool, cost_usd: float | None = None
    ) -> None:
        """Update success/failure streak + cost after a turn completes."""
        if self.governance is None:
            return
        if success:
            self.governance.state.record_success()
        else:
            self.governance.state.record_failure()
        if cost_usd is not None:
            self.governance.state.record_cost(cost_usd)
        # Post-turn enforcement: a cost or failure-streak breach surfaces here.
        self._enforce_governance()

    def handoff_to(
        self,
        target_role: str,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> Handoff:
        """Finalize this session's worker and register a target worker.

        Returns a ``Handoff`` value describing the delegation. The caller
        is responsible for opening a new session against ``target_role``
        to actually run the target agent — this method only routes.

        The current worker transitions to ``COMPLETED`` (so the
        surrounding ``with`` / ``async with`` block does not double-fire
        completion). The new worker is created in the same registry under
        the same ``project_id`` and is left in ``INITIALIZING``.
        """
        ctx_copy = dict(context or {})
        new_worker = self.registry.create(
            role=target_role, phase=self.phase, project_id=self.project_id
        )
        new_worker.transition(
            AgentStatus.INITIALIZING,
            detail=f"handoff from {self.role}: {reason}",
        )

        if not self.worker.is_terminal:
            self.worker.transition(
                AgentStatus.COMPLETED, detail=f"handoff to {target_role}"
            )

        handoff = Handoff(
            source_role=self.role,
            target_role=target_role,
            phase=self.phase,
            reason=reason,
            context=ctx_copy,
            project_id=self.project_id,
            target_worker_id=new_worker.worker_id,
        )
        self._emit_event(
            AgentEvent.completed(
                self.role,
                self.phase,
                detail=f"handoff → {target_role}: {reason}",
            ).with_data({"handoff": handoff.to_dict()})
        )
        logger.info(
            "handoff_issued",
            extra={
                "role": self.role,
                "phase": self.phase,
                "project_id": self.project_id,
                "target_role": target_role,
                "reason": reason,
                "target_worker_id": new_worker.worker_id,
            },
        )
        return handoff

    def _apply_usage_and_check_budget(
        self, model: str, snapshot: UsageSnapshot
    ) -> None:
        if model:
            self.tracker.record_turn(model, snapshot)
            cost = self.tracker.cost_for_turn(model, snapshot)
            try:
                self.usage_sink.record(model, snapshot, cost)
            except Exception:
                logger.exception(
                    "usage_sink.record raised; suppressing to keep session alive"
                )

        budget = self.budget_usd
        if budget is not None and self.tracker.is_over_budget(budget):
            self._emit_event(
                AgentEvent.failed(
                    self.role,
                    self.phase,
                    AgentFailureClass.UNKNOWN,
                    detail=f"budget exceeded: {self.tracker.format_cost()}",
                ).with_data({"budget_usd": budget})
            )
            logger.warning(
                "budget_exceeded",
                extra={
                    "role": self.role,
                    "phase": self.phase,
                    "project_id": self.project_id,
                    "budget_usd": budget,
                    "current_cost_usd": self.tracker.total_cost(),
                    "enforced": self.enforce_budget,
                },
            )
            if self.enforce_budget:
                raise BudgetExceededError(
                    budget_usd=budget,
                    current_cost_usd=self.tracker.total_cost(),
                )

        if self.usage_limits is not None:
            # ``check_limits`` raises ``UsageLimitExceededError`` on the
            # first overrun. We let it propagate so callers can react
            # to per-dimension caps (token quota, tool-call budget)
            # distinctly from cost overruns.
            self.tracker.check_limits(self.usage_limits)


@dataclass
class OrchestrationSession(_SessionBase):
    """Single-agent sync execution context. Created by Orchestrator.session()."""

    circuit_breaker: CircuitBreaker | None = None
    rate_limiter: RateLimiter | None = None

    def run_tool(
        self,
        tool_name: str,
        fn: Callable[[], T],
        *,
        hook_ctx: HookContext | None = None,
    ) -> T:
        """Execute a tool with permission + guardrail + governance + hook checks.

        Order: permission → governance → before_tool hooks → pre-guardrails →
        fn() → post-guardrails → after_tool hooks.

        ``hook_ctx`` is a mutable context shared with the hook chain. When
        omitted the runtime synthesizes a fresh ``HookContext`` so hooks still
        fire — but any mutation hooks attempt on ``ctx.args`` is lost because
        the caller's closure does not see it. Pass a context (and close over
        ``ctx.args`` inside ``fn``) when you want hooks to redact or rewrite
        tool inputs.
        """
        outcome = self.authorize(tool_name)
        if not outcome.allowed:
            raise PermissionDeniedError(outcome)
        self._check_governance_pre_tool()
        ctx = self._resolve_hook_ctx(hook_ctx, tool=tool_name)
        run_before_tool(self.hooks, ctx)
        run_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        result_raw: T = fn()
        run_post_checks(self.guardrails, result_raw, role=self.role, tool=tool_name)
        result: T = run_after_tool(self.hooks, ctx, result_raw)
        return result

    def run_turn(
        self,
        fn: Callable[[], T],
        model: str = "",
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[T], UsageSnapshot] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
        *,
        hook_ctx: HookContext | None = None,
    ) -> tuple[T, UsageSnapshot]:
        """Execute one model turn with circuit-breaker + recovery wiring.

        When ``timeout`` is set, ``fn`` is dispatched to a single-worker
        ``ThreadPoolExecutor`` and waited on with that deadline. The
        executor is created per-turn (cheap) so there is no pool to
        manage across the session.

        On exception: classifies the failure, attempts recovery once,
        and re-raises so the caller decides whether to retry.

        If a ``saver`` + ``thread_id`` are configured and
        ``idempotency_key`` is supplied, this method first looks for a
        prior checkpoint with the same key on the same thread; on a hit
        it returns the cached ``(result, usage)`` without calling
        ``fn``. After a successful execution, a new checkpoint is
        written so a restart can replay through it.
        """
        cached = self._restore_idempotent_turn(idempotency_key)
        if cached is not None:
            cached_result, cached_usage = cached
            # Iteration count still ticks so the cap reflects logical
            # progress through the loop, not just live executions.
            self._check_iteration_cap()
            self._check_governance_pre_turn()
            return cached_result, cached_usage

        if self.rate_limiter is not None:
            self.rate_limiter.acquire_pre_call()

        self._check_iteration_cap()
        self._check_governance_pre_turn()
        ctx = self._resolve_hook_ctx(hook_ctx, model=model)
        # Hooks run BEFORE the model call so they can mutate ctx.prompt
        # in place. Caller's fn() must close over ctx.prompt to see the
        # mutated value — see docs/patterns/hooks.md for the pattern.
        run_before_model(self.hooks, ctx)
        cost_before = self.tracker.total_cost()
        try:
            result = self._invoke_fn(fn, timeout=timeout)
        except CircuitOpenError:
            self._record_governance_turn_outcome(success=False)
            raise
        except TurnTimeoutError:
            self._record_governance_turn_outcome(success=False)
            raise
        except Exception as exc:
            _record_recovery_event(self, exc)
            self._record_governance_turn_outcome(success=False)
            raise

        result = run_after_model(self.hooks, ctx, result)
        snapshot = self._resolve_usage(result, usage, estimate_usage)
        if self.rate_limiter is not None:
            self.rate_limiter.acquire_usage(
                input_tokens=snapshot.input_tokens,
                output_tokens=snapshot.output_tokens,
            )
        self._apply_usage_and_check_budget(model, snapshot)
        self._persist_turn_checkpoint(
            result=result,
            usage=snapshot,
            model=model,
            idempotency_key=idempotency_key,
        )
        cost_delta = self.tracker.total_cost() - cost_before
        self._record_governance_turn_outcome(success=True, cost_usd=cost_delta)
        return result, snapshot

    def _invoke_fn(self, fn: Callable[[], T], *, timeout: float | None) -> T:
        if timeout is None:
            if self.circuit_breaker is not None:
                return self.circuit_breaker.call(fn)
            return fn()

        # Timeout path: dispatch to a one-shot worker thread. We do NOT
        # wrap the executor call in the breaker; the breaker counts the
        # downstream failure if the inner fn raises, and a hard timeout
        # counts as TurnTimeoutError (a session-level concern, not a
        # downstream signal).
        #
        # We deliberately bypass `with ... as ex:` because its __exit__
        # calls shutdown(wait=True), which would block the timeout from
        # returning until the worker thread finishes — defeating the
        # whole point of a hard turn deadline. shutdown(wait=False,
        # cancel_futures=True) lets the timeout propagate immediately;
        # the orphan thread completes in the background (Python has no
        # thread.kill()) but is reclaimed naturally when it returns.
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(self._wrapped_call, fn)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise TurnTimeoutError(timeout) from exc
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    def _wrapped_call(self, fn: Callable[[], T]) -> T:
        if self.circuit_breaker is not None:
            return self.circuit_breaker.call(fn)
        return fn()


@dataclass
class AsyncOrchestrationSession(_SessionBase):
    """Single-agent async execution context. Created by Orchestrator.asession().

    Sibling of OrchestrationSession. Sync helpers (authorize, evaluate_policy,
    evaluate_gate, summary, lifecycle methods) are inherited; only the
    execution path (``arun_turn`` / ``arun_tool``) and the human-in-the-loop
    pause are async.
    """

    circuit_breaker: AsyncCircuitBreaker | None = None
    async_rate_limiter: AsyncRateLimiter | None = None

    async def arun_tool(
        self,
        tool_name: str,
        coro_factory: Callable[[], Awaitable[T]],
        *,
        hook_ctx: HookContext | None = None,
    ) -> T:
        """Async sibling of run_tool.

        Permission + governance are sync; guardrails are awaited when
        they implement ``AsyncGuardrail`` and called inline when they
        are sync ``Guardrail`` instances. Hooks are dispatched both
        sync and async — see ``hooks.py``.
        """
        outcome = self.authorize(tool_name)
        if not outcome.allowed:
            raise PermissionDeniedError(outcome)
        self._check_governance_pre_tool()
        ctx = self._resolve_hook_ctx(hook_ctx, tool=tool_name)
        await arun_before_tool(self.hooks, ctx)
        await arun_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        result_raw: T = await coro_factory()
        await arun_post_checks(
            self.guardrails, result_raw, role=self.role, tool=tool_name
        )
        result: T = await arun_after_tool(self.hooks, ctx, result_raw)
        return result

    async def arun_turn(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        model: str = "",
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[T], UsageSnapshot] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
        *,
        hook_ctx: HookContext | None = None,
    ) -> tuple[T, UsageSnapshot]:
        """Execute one model turn with async circuit-breaker + recovery wiring.

        ``timeout`` is enforced with ``asyncio.wait_for``. Cancellation
        from outside (parent task) is propagated as CancelledError; an
        internal timeout becomes ``TurnTimeoutError``.

        ``idempotency_key`` behaves the same as in ``run_turn``: when the
        session has a saver + thread_id configured, a prior checkpoint
        with the same key short-circuits the call.
        """
        cached = self._restore_idempotent_turn(idempotency_key)
        if cached is not None:
            cached_result, cached_usage = cached
            self._check_iteration_cap()
            self._check_governance_pre_turn()
            return cached_result, cached_usage

        if self.async_rate_limiter is not None:
            await self.async_rate_limiter.acquire_pre_call()

        self._check_iteration_cap()
        self._check_governance_pre_turn()
        ctx = self._resolve_hook_ctx(hook_ctx, model=model)
        await arun_before_model(self.hooks, ctx)
        cost_before = self.tracker.total_cost()
        try:
            result = await self._ainvoke(coro_factory, timeout=timeout)
        except CircuitOpenError:
            self._record_governance_turn_outcome(success=False)
            raise
        except TurnTimeoutError:
            self._record_governance_turn_outcome(success=False)
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _arecord_recovery_event(self, exc)
            self._record_governance_turn_outcome(success=False)
            raise

        result = await arun_after_model(self.hooks, ctx, result)
        snapshot = self._resolve_usage(result, usage, estimate_usage)
        if self.async_rate_limiter is not None:
            await self.async_rate_limiter.acquire_usage(
                input_tokens=snapshot.input_tokens,
                output_tokens=snapshot.output_tokens,
            )
        self._apply_usage_and_check_budget(model, snapshot)
        self._persist_turn_checkpoint(
            result=result,
            usage=snapshot,
            model=model,
            idempotency_key=idempotency_key,
        )
        cost_delta = self.tracker.total_cost() - cost_before
        self._record_governance_turn_outcome(success=True, cost_usd=cost_delta)
        return result, snapshot

    async def arun_turn_stream(
        self,
        chunk_factory: Callable[[], AsyncIterator[str]],
        *,
        model: str = "",
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[str], UsageSnapshot] | None = None,
        timeout: float | None = None,
        hook_ctx: HookContext | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a model turn as a sequence of ``StreamEvent`` values.

        ``chunk_factory`` returns an async iterator over text chunks
        (e.g. SSE deltas from a provider client). Each chunk is reemitted
        as ``StreamEvent.text(delta)``. After upstream exhausts, the
        generator yields a terminal ``StreamEvent.final("completed", usage=...)``
        carrying the resolved usage snapshot.

        Hook chain runs once before iteration begins (``before_model``)
        and once after the aggregated text is computed (``after_model``).
        Per-chunk hooks are intentionally out of scope — they would invert
        the cost model of streaming.

        Cancellation: if the consumer breaks out of the ``async for`` loop,
        the upstream iterator's ``aclose()`` is awaited and
        ``session._last_stream_cancelled`` flips to ``True``. The generator
        does NOT emit a ``final("cancelled")`` event in that case because
        the consumer is no longer listening; check the flag instead.

        On upstream exception: ``error`` + ``final("failed")`` events are
        yielded and the exception is re-raised so callers see it.
        """
        self._check_iteration_cap()
        self._check_governance_pre_turn()
        ctx = self._resolve_hook_ctx(hook_ctx, model=model)
        await arun_before_model(self.hooks, ctx)

        if self.async_rate_limiter is not None:
            await self.async_rate_limiter.acquire_pre_call()

        self._last_stream_cancelled = False
        cost_before = self.tracker.total_cost()
        aggregated: list[str] = []
        upstream: AsyncIterator[str] | None = None

        try:
            try:
                upstream = chunk_factory().__aiter__()
                if timeout is None:
                    async for chunk in upstream:
                        aggregated.append(chunk)
                        yield StreamEvent.text(chunk)
                else:
                    async with asyncio.timeout(timeout):
                        async for chunk in upstream:
                            aggregated.append(chunk)
                            yield StreamEvent.text(chunk)
            except TimeoutError as exc:
                yield StreamEvent.error("timeout", f"stream exceeded {timeout}s")
                yield StreamEvent.final("failed", detail="timeout")
                self._record_governance_turn_outcome(success=False)
                raise TurnTimeoutError(timeout or 0.0) from exc
            except GeneratorExit:
                # Consumer broke the async-for loop. Flag + finally cleanup
                # run; we cannot yield further since the consumer is gone.
                self._last_stream_cancelled = True
                self._record_governance_turn_outcome(success=False)
                logger.info(
                    "stream_cancelled",
                    extra={
                        "role": self.role,
                        "phase": self.phase,
                        "project_id": self.project_id,
                        "chunks_received": len(aggregated),
                    },
                )
                raise
            except asyncio.CancelledError:
                self._last_stream_cancelled = True
                self._record_governance_turn_outcome(success=False)
                raise
            except Exception as exc:
                yield StreamEvent.error(type(exc).__name__, str(exc))
                yield StreamEvent.final("failed", detail=str(exc))
                await _arecord_recovery_event(self, exc)
                self._record_governance_turn_outcome(success=False)
                raise

            full_text = "".join(aggregated)
            transformed = await arun_after_model(self.hooks, ctx, full_text)
            snapshot = self._resolve_usage(transformed, usage, estimate_usage)
            if self.async_rate_limiter is not None:
                await self.async_rate_limiter.acquire_usage(
                    input_tokens=snapshot.input_tokens,
                    output_tokens=snapshot.output_tokens,
                )
            self._apply_usage_and_check_budget(model, snapshot)
            cost_delta = self.tracker.total_cost() - cost_before
            self._record_governance_turn_outcome(success=True, cost_usd=cost_delta)
            yield StreamEvent.final("completed", usage=snapshot.to_dict())
        finally:
            # Close upstream so a generator-based provider client can
            # release its underlying connection. aclose() is idempotent
            # for `async def`-defined generators, so calling it after a
            # naturally-exhausted iteration is a no-op. Swallow any
            # error here so the finally block does not mask the original
            # failure path.
            if upstream is not None:
                aclose = getattr(upstream, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        logger.exception("arun_turn_stream: upstream.aclose() raised")

    async def _ainvoke(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        *,
        timeout: float | None,
    ) -> T:
        if timeout is None:
            return await self._wrapped_acall(coro_factory)

        # asyncio.timeout (Python 3.11+) gives proper structured-concurrency
        # cancellation semantics — the inner task is cancelled exactly
        # once and the context manager re-raises a clean TimeoutError.
        # asyncio.wait_for has a long-standing bug where the inner task
        # can be resurrected after the timeout fires; PEP 789 explains.
        try:
            async with asyncio.timeout(timeout):
                return await self._wrapped_acall(coro_factory)
        except TimeoutError as exc:
            raise TurnTimeoutError(timeout) from exc

    async def _wrapped_acall(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        if self.circuit_breaker is not None:
            return await self.circuit_breaker.call(coro_factory)
        return await coro_factory()

    async def arun_parallel_tools(
        self,
        coro_factories: Sequence[Callable[[], Awaitable[Any]]],
        *,
        timeout: float | None = None,
    ) -> list[Any]:
        """Run several tool calls concurrently with structured concurrency.

        Uses ``asyncio.TaskGroup`` so any child failure cancels its
        siblings and surfaces as ``ExceptionGroup`` to the caller —
        no orphan tasks, no swallowed exceptions. ``timeout`` (if
        given) applies to the whole group via ``asyncio.timeout``.

        Returns results in input order. Each ``coro_factory`` is a
        zero-arg callable that returns an awaitable; this matches the
        contract used elsewhere in the session API and means callers
        can build the coroutine lazily inside the group.
        """
        if not coro_factories:
            return []

        results: list[Any] = [None] * len(coro_factories)

        async def _runner(idx: int, factory: Callable[[], Awaitable[Any]]) -> None:
            results[idx] = await factory()

        try:
            if timeout is None:
                async with asyncio.TaskGroup() as tg:
                    for i, f in enumerate(coro_factories):
                        tg.create_task(_runner(i, f))
            else:
                async with asyncio.timeout(timeout):
                    async with asyncio.TaskGroup() as tg:
                        for i, f in enumerate(coro_factories):
                            tg.create_task(_runner(i, f))
        except TimeoutError as exc:
            raise TurnTimeoutError(timeout or 0.0) from exc
        return results

    async def pause_for_input(self, prompt: str) -> str:
        """Mark the worker WAITING_FOR_INPUT and await an external response.

        Returns a future the caller resolves via
        ``session.provide_input(value)`` from elsewhere in the program.
        Use this to wire human-in-the-loop or out-of-band approvals
        without leaving the session machinery.
        """
        if self.worker.is_terminal:
            raise RuntimeError(
                f"cannot pause a terminal session ({self.worker.status.value})"
            )
        if self._pending_input is not None:
            raise RuntimeError("a previous pause_for_input is still pending")

        self.worker.transition(AgentStatus.WAITING_FOR_INPUT, detail=prompt)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_input = future
        try:
            value = await future
        finally:
            self._pending_input = None
        if not self.worker.is_terminal:
            self.worker.transition(AgentStatus.RUNNING, detail="input received")
        return value

    def provide_input(self, value: str) -> None:
        """Resolve the most recent pause_for_input future with ``value``."""
        if self._pending_input is None:
            raise RuntimeError("no pause_for_input in flight")
        if not self._pending_input.done():
            self._pending_input.set_result(value)

    _pending_input: asyncio.Future[str] | None = field(
        default=None, init=False, repr=False
    )


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


# `Orchestrator` is the legacy 0.1.x name; `AgentSession` is canonical.
# Subclass (not bare alias) so we can emit DeprecationWarning on the
# first instantiation in a process. Removed in 0.3.0.
class Orchestrator(AgentSession):
    """Deprecated alias for ``AgentSession``. Removed in 0.3.0."""

    _deprecation_emitted: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if not Orchestrator._deprecation_emitted:
            import warnings

            warnings.warn(
                "Orchestrator is a deprecated alias for AgentSession and will "
                "be removed in 0.3.0. Replace `Orchestrator(...)` with "
                "`AgentSession(...)` — the constructor and behavior are "
                "identical.",
                DeprecationWarning,
                stacklevel=2,
            )
            Orchestrator._deprecation_emitted = True
        super().__init__(*args, **kwargs)


__all__ = [
    "AgentSession",
    "AsyncOrchestrationSession",
    "MaxIterationsExceededError",
    "OrchestrationSession",
    "Orchestrator",
    "PermissionDeniedError",
    "TurnTimeoutError",
]
