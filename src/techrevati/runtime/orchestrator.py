"""
Orchestrator — single execution loop wiring the runtime primitives together.

Pairs an agent session with lifecycle tracking, usage accounting,
circuit-breaker protection, automatic failure classification, permission
gating, and policy evaluation. Use the primitives standalone or use
``AgentSession.session()`` (sync) / ``AgentSession.asession()`` (async)
to get all of them wired in.

Example (sync):
    from techrevati.runtime import AgentSession, UsageSnapshot

    agent = AgentSession(role="writer", phase="draft", project_id=1)
    with agent.session() as session:
        text, usage = session.run_turn(
            lambda: call_model(prompt),
            model="model-a",
            usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
        )
    print(session.summary())

Example (async):
    async with agent.asession() as session:
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
import math
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
from techrevati.runtime.compliance.audit_log import AuditLogSink
from techrevati.runtime.compliance.kit import EUAIActComplianceKit
from techrevati.runtime.governance import GovernanceBreachError, GovernancePlane
from techrevati.runtime.guardrails import (
    AsyncGuardrail,
    Guardrail,
    GuardrailViolatedError,
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
    arun_before_handoff,
    arun_before_model,
    arun_before_tool,
    run_after_model,
    run_after_tool,
    run_before_handoff,
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
from techrevati.runtime.rate_limit import (
    AsyncRateLimiter,
    RateLimiter,
    RateLimitExceededError,
)
from techrevati.runtime.retry_policy import (
    FailureScenario,
    RecoveryContext,
    RecoveryResult,
    aattempt_recovery,
    attempt_recovery,
    classify_exception,
)
from techrevati.runtime.routing import ProviderRouter
from techrevati.runtime.sinks import (
    EventSink,
    FanoutEventSink,
    FanoutUsageSink,
    NoopEventSink,
    NoopUsageSink,
    UsageSink,
)
from techrevati.runtime.streaming import StreamEvent
from techrevati.runtime.usage_tracking import (
    BudgetExceededError,
    UsageLimitExceededError,
    UsageLimits,
    UsageSnapshot,
    UsageTracker,
)

logger = logging.getLogger("techrevati.runtime.orchestrator")
logger.addHandler(logging.NullHandler())

T = TypeVar("T")
_PROMPT_REJECTION_MARKERS = (
    "prompt rejected",
    "prompt rejection",
    "content policy",
    "content filter",
    "safety policy",
    "blocked by safety",
    "moderation",
    "jailbreak",
    "unsafe prompt",
    "disallowed content",
)


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

    Default cap of 25 prevents runaway agent loops; stopping conditions are a
    production-readiness requirement.
    """

    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations
        super().__init__(
            f"session reached max_iterations={max_iterations}; "
            "raise the cap or shorten the loop"
        )


def _validate_non_empty_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_optional_non_empty_str(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _validate_non_empty_str(field_name, value)


def _validate_project_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("project_id must be an integer or None")
    if value < 0:
        raise ValueError("project_id must be non-negative")
    return value


def _validate_budget_usd(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("budget_usd must be a number or None")
    budget = float(value)
    if not math.isfinite(budget):
        raise ValueError("budget_usd must be finite")
    if budget < 0:
        raise ValueError("budget_usd must be non-negative")
    return budget


def _validate_bool(field_name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


def _validate_max_iterations(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_iterations must be an integer")
    if value < 0:
        raise ValueError("max_iterations must be non-negative")
    return value


def _record_recovery_event(session: OrchestrationSession, exc: Exception) -> None:
    """Classify exc and emit a recovery-attempted event on the session.

    Shared between sync and async run_turn so behavior stays in lock-step.
    """
    scenario = classify_exception(exc)
    recovery_result = attempt_recovery(scenario, session.recovery)
    _emit_recovery_events(session, scenario, recovery_result)
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
    _emit_recovery_events(session, scenario, recovery_result)
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


def _emit_recovery_events(
    session: _SessionBase,
    scenario: FailureScenario,
    recovery_result: RecoveryResult,
) -> None:
    detail = f"{scenario.value}: {recovery_result.outcome}"
    session._emit_event(
        AgentEvent.recovery_attempted(
            session.role,
            session.phase,
            detail=detail,
        )
    )
    data = recovery_result.to_dict()
    data["scenario"] = scenario.value
    if recovery_result.outcome == "recovered":
        event = AgentEvent.recovery_succeeded(
            session.role, session.phase, detail=detail, data=data
        )
    elif recovery_result.outcome == "partial_recovery":
        event = AgentEvent.recovery_failed(
            session.role, session.phase, detail=detail, data=data
        )
    else:
        event = AgentEvent.recovery_escalated(
            session.role, session.phase, detail=detail, data=data
        )
    session._emit_event(event)


@dataclass
class AgentSession:
    """Factory for sessions. Holds shared, long-lived components.

    Components are optional; the simplest invocation is
    ``AgentSession(role=..., phase=...)``. Provide ``circuit_breaker``
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
    audit_log: AuditLogSink | None = None
    compliance: EUAIActComplianceKit | None = None

    def __post_init__(self) -> None:
        self.role = _validate_non_empty_str("role", self.role)
        self.phase = _validate_non_empty_str("phase", self.phase)
        self.project_id = _validate_project_id(self.project_id)
        self.budget_usd = _validate_budget_usd(self.budget_usd)
        self.enforce_budget = _validate_bool("enforce_budget", self.enforce_budget)
        self.max_iterations = _validate_max_iterations(self.max_iterations)

    def _governance_for_session(self) -> GovernancePlane | None:
        if self.governance is None:
            return None
        return self.governance.for_session()

    def _extra_event_sinks(self) -> list[EventSink]:
        """Compliance event sinks (audit log + incident sink) to fan out.

        The caller's own sink keeps receiving events unchanged; the tamper-
        evident ``AuditLogSink`` (EU AI Act Article 12) and the incident sink
        (Articles 26/73) are appended alongside. De-duplicated by identity so a
        standalone ``audit_log`` shared with ``compliance`` is not double-fed.
        """
        extra: list[EventSink] = []
        seen: set[int] = set()
        candidates: list[EventSink] = []
        if self.audit_log is not None:
            candidates.append(self.audit_log)
        if self.compliance is not None:
            candidates.extend(self.compliance.event_sinks())
        for sink in candidates:
            if id(sink) not in seen:
                seen.add(id(sink))
                extra.append(sink)
        return extra

    def _extra_usage_sinks(self) -> list[UsageSink]:
        extra: list[UsageSink] = []
        seen: set[int] = set()
        candidates: list[UsageSink] = []
        if self.audit_log is not None:
            candidates.append(self.audit_log)
        if self.compliance is not None:
            candidates.extend(self.compliance.usage_sinks())
        for sink in candidates:
            if id(sink) not in seen:
                seen.add(id(sink))
                extra.append(sink)
        return extra

    def _event_sink_for_session(self) -> EventSink:
        extra = self._extra_event_sinks()
        if not extra:
            return self.event_sink
        return FanoutEventSink([self.event_sink, *extra])

    def _usage_sink_for_session(self) -> UsageSink:
        extra = self._extra_usage_sinks()
        if not extra:
            return self.usage_sink
        return FanoutUsageSink([self.usage_sink, *extra])

    def _guardrails_for_session(self) -> list[Guardrail | AsyncGuardrail]:
        """Prepend the compliance kit's output guardrails (Article 15)."""
        extra = self.compliance.guardrails() if self.compliance is not None else []
        return [*extra, *self.guardrails]

    def _hooks_for_session(self) -> list[HookLike]:
        """Prepend the compliance kit's input-sanitization hooks (Article 15)."""
        extra = self.compliance.hooks() if self.compliance is not None else []
        return [*extra, *self.hooks]

    def _assert_compliance_deployable(self) -> None:
        """Block session creation on an unacceptable residual risk (Article 9(4))."""
        if self.compliance is not None:
            self.compliance.assert_deployable()

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
        thread_id = _validate_optional_non_empty_str("thread_id", thread_id)
        self._assert_compliance_deployable()
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
            guardrails=self._guardrails_for_session(),
            event_sink=self._event_sink_for_session(),
            usage_sink=self._usage_sink_for_session(),
            phase=self.phase,
            role=self.role,
            project_id=self.project_id,
            thread_id=thread_id,
            saver=self.saver,
            provider_router=self.provider_router,
            rate_limiter=self.rate_limiter,
            usage_limits=self.usage_limits,
            governance=self._governance_for_session(),
            hooks=self._hooks_for_session(),
        )
        session._emit_event(AgentEvent.started(self.role, self.phase))

        try:
            yield session
        except GovernanceBreachError:
            # Terminal — record FAILED with governance class, do NOT recover.
            session.fail(
                detail="governance breach",
                failure_class=AgentFailureClass.GOVERNANCE_BREACH,
            )
            raise
        except Exception as exc:
            session.fail(
                detail=_safe_exception_detail(exc),
                failure_class=_failure_class_for_exception(exc),
            )
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
        thread_id = _validate_optional_non_empty_str("thread_id", thread_id)
        self._assert_compliance_deployable()
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
            guardrails=self._guardrails_for_session(),
            event_sink=self._event_sink_for_session(),
            usage_sink=self._usage_sink_for_session(),
            phase=self.phase,
            role=self.role,
            project_id=self.project_id,
            thread_id=thread_id,
            saver=self.saver,
            provider_router=self.provider_router,
            async_rate_limiter=self.async_rate_limiter,
            usage_limits=self.usage_limits,
            governance=self._governance_for_session(),
            hooks=self._hooks_for_session(),
        )
        session._emit_event(AgentEvent.started(self.role, self.phase))

        try:
            yield session
        except asyncio.CancelledError:
            session.cancel(detail="async session cancelled")
            raise
        except GovernanceBreachError:
            session.fail(
                detail="governance breach",
                failure_class=AgentFailureClass.GOVERNANCE_BREACH,
            )
            raise
        except Exception as exc:
            session.fail(
                detail=_safe_exception_detail(exc),
                failure_class=_failure_class_for_exception(exc),
            )
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

    def __post_init__(self) -> None:
        self.role = _validate_non_empty_str("role", self.role)
        self.phase = _validate_non_empty_str("phase", self.phase)
        self.project_id = _validate_project_id(self.project_id)
        self.budget_usd = _validate_budget_usd(self.budget_usd)
        self.enforce_budget = _validate_bool("enforce_budget", self.enforce_budget)
        self.max_iterations = _validate_max_iterations(self.max_iterations)
        self.thread_id = _validate_optional_non_empty_str("thread_id", self.thread_id)

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

    def _resolve_handoff_hook_ctx(
        self,
        ctx: HookContext | None,
        *,
        target_role: str,
        reason: str,
        context: dict[str, Any] | None,
    ) -> HookContext:
        handoff_context: dict[str, Any] = {} if context is None else context
        if ctx is None:
            return HookContext(
                role=self.role,
                phase=self.phase,
                extra={
                    "target_role": target_role,
                    "reason": reason,
                    "context": handoff_context,
                    "project_id": self.project_id,
                },
            )
        ctx.role = self.role
        ctx.phase = self.phase
        ctx.extra["target_role"] = target_role
        ctx.extra["reason"] = reason
        ctx.extra["context"] = handoff_context
        ctx.extra["project_id"] = self.project_id
        return ctx

    def _issue_handoff_from_ctx(self, ctx: HookContext) -> Handoff:
        target_role = ctx.extra.get("target_role")
        if not isinstance(target_role, str):
            raise TypeError("target_role must be a string")
        reason = ctx.extra.get("reason")
        if not isinstance(reason, str):
            raise TypeError("reason must be a string")
        pending_handoff = Handoff(
            source_role=self.role,
            target_role=target_role,
            phase=self.phase,
            reason=reason,
            context=ctx.extra.get("context", {}),
            project_id=self.project_id,
        )
        new_worker = self.registry.create(
            role=pending_handoff.target_role,
            phase=pending_handoff.phase,
            project_id=pending_handoff.project_id,
        )
        new_worker.transition(
            AgentStatus.INITIALIZING,
            detail=f"handoff from {self.role}: {pending_handoff.reason}",
        )

        if not self.worker.is_terminal:
            self.worker.transition(
                AgentStatus.COMPLETED,
                detail=f"handoff to {pending_handoff.target_role}",
            )

        handoff = Handoff(
            source_role=pending_handoff.source_role,
            target_role=pending_handoff.target_role,
            phase=pending_handoff.phase,
            reason=pending_handoff.reason,
            context=pending_handoff.context,
            project_id=pending_handoff.project_id,
            target_worker_id=new_worker.worker_id,
        )
        self._emit_event(
            AgentEvent.completed(
                self.role,
                self.phase,
                detail=(
                    f"handoff → {pending_handoff.target_role}: {pending_handoff.reason}"
                ),
            ).with_data({"handoff": handoff.to_dict()})
        )
        logger.info(
            "handoff_issued",
            extra={
                "role": self.role,
                "phase": self.phase,
                "project_id": self.project_id,
                "target_role": pending_handoff.target_role,
                "reason": pending_handoff.reason,
                "target_worker_id": new_worker.worker_id,
            },
        )
        return handoff

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
                AgentFailureClass.CANCELLED,
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

    def _sink_failure_event(self, component: str, exc: Exception) -> AgentEvent:
        return AgentEvent.failed(
            self.role,
            self.phase,
            AgentFailureClass.DEPENDENCY_FAILED,
            detail=f"{component} failed; session continued",
        ).with_data({"component": component, "error_type": type(exc).__name__})

    def _append_local_diagnostic_event(self, event: AgentEvent) -> None:
        if self.project_id is not None and event.project_id is None:
            event = event.with_project(self.project_id)
        self.events.append(event)

    def _emit_event(self, event: AgentEvent) -> None:
        """Append to the session event log AND forward to the configured sink.

        Use this everywhere instead of ``self.events.append(event)`` so
        observability stays consistent. The sink call is wrapped in a
        try/except so a misbehaving sink can't break the session.
        """
        if self.project_id is not None and event.project_id is None:
            event = event.with_project(self.project_id)
        self.events.append(event)
        try:
            self.event_sink.emit(event)
        except Exception as exc:
            self._append_local_diagnostic_event(
                self._sink_failure_event("event_sink", exc)
            )
            logger.error(
                "event_sink.emit raised; suppressing to keep session alive",
                extra={
                    "role": self.role,
                    "phase": self.phase,
                    "project_id": self.project_id,
                    "error_type": type(exc).__name__,
                },
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

    def _emit_tool_blocked(
        self,
        tool_name: str,
        *,
        kind: str,
        stage: str | None = None,
        guardrails: list[str] | None = None,
    ) -> None:
        data: dict[str, Any] = {"tool": tool_name, "kind": kind}
        if stage is not None:
            data["stage"] = stage
        if guardrails:
            data["guardrails"] = guardrails
        self._emit_event(
            AgentEvent.blocked(
                self.role,
                self.phase,
                detail=f"{kind} blocked tool call",
                data=data,
            )
        )

    def _emit_guardrail_blocked(self, err: GuardrailViolatedError) -> None:
        self._emit_tool_blocked(
            err.tool,
            kind="guardrail",
            stage=err.stage,
            guardrails=[violation.guardrail for violation in err.violations],
        )

    def _emit_tool_failed(self, tool_name: str) -> None:
        self._emit_event(
            AgentEvent.failed(
                self.role,
                self.phase,
                AgentFailureClass.TOOL_ERROR,
                detail=f"tool execution failed: {tool_name}",
            ).with_data({"tool": tool_name})
        )

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
        *,
        hook_ctx: HookContext | None = None,
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
        ctx = self._resolve_handoff_hook_ctx(
            hook_ctx,
            target_role=target_role,
            reason=reason,
            context=context,
        )
        run_before_handoff(self.hooks, ctx)
        return self._issue_handoff_from_ctx(ctx)

    def _apply_usage_and_check_budget(
        self, model: str, snapshot: UsageSnapshot
    ) -> None:
        if model:
            self.tracker.record_turn(model, snapshot)
            cost = self.tracker.cost_for_turn(model, snapshot)
            try:
                self.usage_sink.record(model, snapshot, cost)
            except Exception as exc:
                self._emit_event(self._sink_failure_event("usage_sink", exc))
                logger.error(
                    "usage_sink.record raised; suppressing to keep session alive",
                    extra={
                        "role": self.role,
                        "phase": self.phase,
                        "project_id": self.project_id,
                        "error_type": type(exc).__name__,
                    },
                )

        budget = self.budget_usd
        if budget is not None and self.tracker.is_over_budget(budget):
            self._emit_event(
                AgentEvent.failed(
                    self.role,
                    self.phase,
                    AgentFailureClass.RATE_LIMIT,
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
            try:
                self.tracker.check_limits(self.usage_limits)
            except UsageLimitExceededError as exc:
                self._emit_event(
                    AgentEvent.failed(
                        self.role,
                        self.phase,
                        AgentFailureClass.RATE_LIMIT,
                        detail=f"usage limit exceeded: {exc.limit_name}",
                    ).with_data(
                        {
                            "limit_name": exc.limit_name,
                            "observed": exc.observed,
                            "ceiling": exc.ceiling,
                        }
                    )
                )
                raise


@dataclass
class OrchestrationSession(_SessionBase):
    """Single-agent sync execution context. Created by AgentSession.session()."""

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
            self._emit_tool_blocked(tool_name, kind="permission")
            raise PermissionDeniedError(outcome)
        self._check_governance_pre_tool()
        ctx = self._resolve_hook_ctx(hook_ctx, tool=tool_name)
        run_before_tool(self.hooks, ctx)
        try:
            run_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc)
            raise
        self._emit_event(AgentEvent.tool_called(self.role, self.phase, tool_name))
        try:
            result_raw: T = fn()
        except Exception:
            self._emit_tool_failed(tool_name)
            raise
        try:
            run_post_checks(self.guardrails, result_raw, role=self.role, tool=tool_name)
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc)
            raise
        result: T = run_after_tool(self.hooks, ctx, result_raw)
        self._emit_event(AgentEvent.tool_completed(self.role, self.phase, tool_name))
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
        except TurnTimeoutError as exc:
            _record_recovery_event(self, exc)
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
    """Single-agent async execution context. Created by AgentSession.asession().

    Sibling of OrchestrationSession. Sync helpers (authorize, evaluate_policy,
    evaluate_gate, summary, lifecycle methods) are inherited; only the
    execution path (``arun_turn`` / ``arun_tool``) and the human-in-the-loop
    pause are async.
    """

    circuit_breaker: AsyncCircuitBreaker | None = None
    async_rate_limiter: AsyncRateLimiter | None = None

    async def ahandoff_to(
        self,
        target_role: str,
        reason: str,
        context: dict[str, Any] | None = None,
        *,
        hook_ctx: HookContext | None = None,
    ) -> Handoff:
        """Async handoff helper that awaits async ``before_handoff`` hooks."""
        ctx = self._resolve_handoff_hook_ctx(
            hook_ctx,
            target_role=target_role,
            reason=reason,
            context=context,
        )
        await arun_before_handoff(self.hooks, ctx)
        return self._issue_handoff_from_ctx(ctx)

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
            self._emit_tool_blocked(tool_name, kind="permission")
            raise PermissionDeniedError(outcome)
        self._check_governance_pre_tool()
        ctx = self._resolve_hook_ctx(hook_ctx, tool=tool_name)
        await arun_before_tool(self.hooks, ctx)
        try:
            await arun_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc)
            raise
        self._emit_event(AgentEvent.tool_called(self.role, self.phase, tool_name))
        try:
            result_raw: T = await coro_factory()
        except Exception:
            self._emit_tool_failed(tool_name)
            raise
        try:
            await arun_post_checks(
                self.guardrails, result_raw, role=self.role, tool=tool_name
            )
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc)
            raise
        result: T = await arun_after_tool(self.hooks, ctx, result_raw)
        self._emit_event(AgentEvent.tool_completed(self.role, self.phase, tool_name))
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
        except TurnTimeoutError as exc:
            await _arecord_recovery_event(self, exc)
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
                timeout_error = TurnTimeoutError(timeout or 0.0)
                # Record the failure outcome BEFORE yielding the terminal
                # event: an idiomatic consumer that breaks the async-for on
                # seeing ``final`` would otherwise throw GeneratorExit at that
                # yield and skip the recovery/governance recording entirely.
                await _arecord_recovery_event(self, timeout_error)
                self._record_governance_turn_outcome(success=False)
                yield StreamEvent.error("timeout", f"stream exceeded {timeout}s")
                yield StreamEvent.final("failed", detail="timeout")
                raise timeout_error from exc
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
                safe_detail = _safe_exception_detail(exc)
                # Record before the terminal yield (see TimeoutError branch):
                # a consumer that stops iterating at ``final`` must not be able
                # to skip the recovery/governance recording.
                await _arecord_recovery_event(self, exc)
                self._record_governance_turn_outcome(success=False)
                yield StreamEvent.error(type(exc).__name__, safe_detail)
                yield StreamEvent.final("failed", detail=safe_detail)
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
                    except Exception as exc:
                        self._append_local_diagnostic_event(
                            self._sink_failure_event("stream_upstream", exc)
                        )
                        logger.error(
                            "arun_turn_stream: upstream.aclose() raised",
                            extra={
                                "role": self.role,
                                "phase": self.phase,
                                "project_id": self.project_id,
                                "error_type": type(exc).__name__,
                            },
                        )

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
        self._emit_event(
            AgentEvent.blocked(
                self.role,
                self.phase,
                detail="waiting for input",
                data={"kind": "human_input"},
            )
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_input = future
        try:
            value = await future
        finally:
            self._pending_input = None
        if not self.worker.is_terminal:
            self.worker.transition(AgentStatus.RUNNING, detail="input received")
            self._emit_event(
                AgentEvent.ready(
                    self.role,
                    self.phase,
                    detail="input received",
                    data={"kind": "human_input"},
                )
            )
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


def _failure_class_for_exception(exc: Exception) -> AgentFailureClass:
    """Classify terminal session exceptions into the public event taxonomy."""
    if isinstance(exc, BudgetExceededError | UsageLimitExceededError):
        return AgentFailureClass.RATE_LIMIT
    if isinstance(exc, RateLimitExceededError):
        return AgentFailureClass.RATE_LIMIT
    if isinstance(exc, MaxIterationsExceededError):
        return AgentFailureClass.GOVERNANCE_BREACH
    if isinstance(exc, PermissionDeniedError):
        return AgentFailureClass.PERMISSION_DENIED
    if isinstance(exc, GuardrailViolatedError):
        return AgentFailureClass.GUARDRAIL_VIOLATION
    scenario = classify_exception(exc)
    if scenario != FailureScenario.LLM_ERROR:
        return _scenario_to_class(scenario)
    if _is_prompt_rejection_exception(exc):
        return AgentFailureClass.PROMPT_REJECTION
    if isinstance(exc, ValueError | TypeError):
        return AgentFailureClass.VALIDATION_ERROR
    return _scenario_to_class(scenario)


def _is_prompt_rejection_exception(exc: Exception) -> bool:
    """Return True when an exception chain looks like a prompt safety rejection."""
    seen: set[int] = set()
    cursor: BaseException | None = exc
    while cursor is not None and id(cursor) not in seen:
        seen.add(id(cursor))
        message = str(cursor).lower()
        if any(marker in message for marker in _PROMPT_REJECTION_MARKERS):
            return True
        nxt: BaseException | None = cursor.__cause__
        if nxt is None and not cursor.__suppress_context__:
            nxt = cursor.__context__
        cursor = nxt
    return False


def _safe_exception_detail(exc: Exception) -> str:
    """Describe a terminal exception without copying its message into events."""
    return f"{type(exc).__name__} raised"


# `Orchestrator` is the legacy 0.1.x name; `AgentSession` is canonical.
# Subclass (not bare alias) so we can emit DeprecationWarning on the
# first instantiation in a process. Kept through 0.3.x for compatibility.
class Orchestrator(AgentSession):
    """Deprecated compatibility alias for ``AgentSession``.

    Kept through the 0.3.x line so existing callers can upgrade without
    a hard break. New code should construct ``AgentSession`` directly.
    """

    _deprecation_emitted: ClassVar[bool] = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if not Orchestrator._deprecation_emitted:
            import warnings

            warnings.warn(
                "Orchestrator is a deprecated alias for AgentSession and will "
                "be removed no earlier than 0.4.0. Replace `Orchestrator(...)` "
                "with `AgentSession(...)` — the constructor and behavior are "
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
