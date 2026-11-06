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
import contextvars
import copy
import functools
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, NamedTuple, TypeVar

from techrevati.runtime._classify import (
    _is_prompt_rejection_exception,
    _safe_exception_detail,
    _scenario_to_class,
)
from techrevati.runtime._internal import (
    _validate_bool,
    _validate_non_empty_str,
    _validate_optional_non_empty_str,
    _validate_project_id,
)
from techrevati.runtime.agent_events import AgentEvent, AgentFailureClass
from techrevati.runtime.agent_lifecycle import (
    AgentRegistry,
    AgentStatus,
    AgentWorker,
)
from techrevati.runtime.checkpoint import CheckpointSaver, StepCheckpointSaver
from techrevati.runtime.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitBreaker,
    CircuitOpenError,
)
from techrevati.runtime.compliance.audit_log import AuditLogSink
from techrevati.runtime.compliance.kit import EUAIActComplianceKit
from techrevati.runtime.governance import (
    GovernanceBreachError,
    GovernancePlane,
    safe_tool_call_signature,
)
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


class PermissionDeniedError(Exception):
    """Raised when a tool is blocked by the configured PermissionEnforcer."""

    def __init__(self, outcome: PermissionOutcome) -> None:
        self.outcome = outcome
        super().__init__(outcome.reason or "permission denied")

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.outcome,))


class TurnTimeoutError(Exception):
    """Raised when a sync or async turn exceeds the configured timeout.

    For sync callers, this wraps the underlying ``concurrent.futures.TimeoutError``;
    for async callers, the original ``asyncio.TimeoutError`` is re-raised
    as this type to give a single error class across both code paths.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"turn exceeded timeout of {timeout_seconds:.3f}s")

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.timeout_seconds,))


class ToolTimeoutError(TurnTimeoutError):
    """Raised when ``run_tool`` / ``arun_tool`` exceeds its ``timeout``.

    Subclasses :class:`TurnTimeoutError` so a single ``except`` covers both.
    An async tool is cancelled. A sync tool cannot be killed, so its worker
    thread finishes in the background: a tool whose side effects must not
    outlive the deadline has to enforce its own timeout too.
    """

    def __init__(self, tool_name: str, timeout_seconds: float) -> None:
        super().__init__(timeout_seconds)
        self.tool_name = tool_name
        self.args = (f"tool {tool_name!r} exceeded timeout of {timeout_seconds:.3f}s",)

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.tool_name, self.timeout_seconds))


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

    def __reduce__(self) -> tuple[Any, ...]:
        return (type(self), (self.max_iterations,))


# How long a timed-out stream's upstream gets to close before it is abandoned.
_STREAM_CLOSE_GRACE_SECONDS = 5.0

# Failures worth retrying on another provider. Anything else, such as a context
# overflow, would fail the same way on the next provider too.
DEFAULT_FAILOVER_SCENARIOS: frozenset[FailureScenario] = frozenset(
    (FailureScenario.PROVIDER_FAILURE, FailureScenario.LLM_TIMEOUT)
)


class _ReplayedTurn(NamedTuple):
    result: Any
    usage: UsageSnapshot
    model: str


class _ReplayedTool(NamedTuple):
    result: Any


def _tool_step_key(tool_name: str, idempotency_key: str) -> str:
    """Encode the pair unambiguously, so ("a", "b:c") and ("a:b", "c") differ."""
    return "tool:" + json.dumps([tool_name, idempotency_key], ensure_ascii=True)


def _call_with_deadline(
    fn: Callable[[], T], timeout: float, on_timeout: Callable[[], Exception]
) -> T:
    """Run ``fn`` on a one-shot worker thread and wait at most ``timeout``.

    The caller's context variables travel with the call, so tracing spans and
    similar ambient state behave the same with and without a deadline.

    ``with ThreadPoolExecutor()`` is deliberately avoided: its ``__exit__`` waits
    for the worker, which would block the deadline until ``fn`` returned. Python
    cannot kill a thread, so on a timeout the worker finishes in the background.
    ``concurrent.futures.wait`` keeps a ``TimeoutError`` raised by ``fn`` itself
    distinct from the deadline expiring.
    """
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        context = contextvars.copy_context()
        future = ex.submit(context.run, fn)
        done, _ = concurrent.futures.wait((future,), timeout=timeout)
        if not done:
            future.cancel()
            raise on_timeout()
        return future.result()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _validate_thread_id(value: str | None) -> str | None:
    """Reject a thread id no saver can store, before any turn has been paid for."""
    thread_id = _validate_optional_non_empty_str("thread_id", value)
    if thread_id is not None:
        try:
            thread_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("thread_id must be valid Unicode text") from exc
    return thread_id


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
        self, *, thread_id: str | None = None, handoff: Handoff | None = None
    ) -> Iterator[OrchestrationSession]:
        """Open a single-agent sync session.

        On clean exit: worker → COMPLETED if still running. On exception:
        worker → FAILED with the error classified into an AgentFailureClass.

        If ``thread_id`` is supplied and the orchestrator has a ``saver``,
        the session writes a checkpoint after each turn and an
        ``idempotency_key`` on ``run_turn`` makes that turn replay-safe.

        Pass the ``Handoff`` returned by ``handoff_to`` as ``handoff`` to run
        on the worker it reserved; otherwise that worker stays INITIALIZING.
        """
        thread_id = _validate_thread_id(thread_id)
        self._assert_compliance_deployable()
        worker = self._start_worker(handoff)
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
        self, *, thread_id: str | None = None, handoff: Handoff | None = None
    ) -> AsyncIterator[AsyncOrchestrationSession]:
        """Open a single-agent async session.

        Mirrors ``session()`` but uses async primitives. ``CancelledError``
        from anywhere inside the ``async with`` body transitions the
        worker to CANCELLED instead of FAILED, and is re-raised.

        The same ``thread_id`` / ``saver`` contract from ``session()``
        applies; pair with ``arun_turn(..., idempotency_key=...)`` for
        replay-safe async turns, and pass ``handoff`` to run on the worker a
        ``Handoff`` reserved.
        """
        thread_id = _validate_thread_id(thread_id)
        self._assert_compliance_deployable()
        worker = self._start_worker(handoff)
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

    def _start_worker(self, handoff: Handoff | None = None) -> AgentWorker:
        """Create a worker, or adopt a handoff's, and walk it to RUNNING."""
        if handoff is not None:
            return self._adopt_handoff_worker(handoff)
        worker = self.registry.create(
            role=self.role, phase=self.phase, project_id=self.project_id
        )
        worker.transition(AgentStatus.INITIALIZING)
        worker.transition(AgentStatus.RUNNING)
        return worker

    def _adopt_handoff_worker(self, handoff: Handoff) -> AgentWorker:
        if handoff.target_role != self.role:
            raise ValueError(
                f"handoff targets role {handoff.target_role!r}, "
                f"this session runs {self.role!r}"
            )
        worker = (
            self.registry.get(handoff.target_worker_id)
            if handoff.target_worker_id
            else None
        )
        if worker is None or worker.status != AgentStatus.INITIALIZING:
            raise ValueError(
                "handoff has no pending worker in this registry; "
                "was it already adopted, or issued by another registry?"
            )
        worker.transition(AgentStatus.RUNNING, detail="handoff adopted")
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
    # Model that booked each idempotency key in this session. Guards against a
    # retry counting the same spend twice, and tells fallback who answered.
    _booked_turns: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _last_model_call_error: Exception | None = field(
        default=None, init=False, repr=False
    )

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
        # The current call's arguments always win: a reused context must not show
        # guard hooks the arguments of an earlier call.
        if args is not None:
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
    ) -> _ReplayedTurn | None:
        """Return the recorded turn if this idempotency_key already ran."""
        if not (idempotency_key and self.thread_id and self.saver is not None):
            return None
        # Bounded scan over the most recent checkpoints. 100 is plenty for a
        # typical session; callers expecting truly long-lived threads should
        # cache the lookup outside the runtime.
        for cp in self.saver.list(self.thread_id, limit=100):
            if cp.metadata.get("idempotency_key") == idempotency_key:
                return _ReplayedTurn(
                    result=cp.state.get("result"),
                    usage=UsageSnapshot.from_dict(cp.state.get("usage") or {}),
                    model=str(cp.metadata.get("model") or ""),
                )
        return None

    def _replay_turn(
        self, replayed: _ReplayedTurn, idempotency_key: str | None
    ) -> tuple[Any, UsageSnapshot]:
        """Count a recorded turn against this session and return its result.

        The money was spent, so a resumed session has to see it, or a restart
        would reset the budget. Sinks are skipped because they saw the original
        turn; re-recording there would double-count it. A key this session has
        already booked is not counted again.
        """
        if idempotency_key not in self._booked_turns:
            self.tracker.record_turn(replayed.model, replayed.usage)
            cost = self.tracker.cost_for_turn(replayed.model, replayed.usage)
            self._record_governance_outcome(success=True, cost_usd=cost)
            if idempotency_key:
                self._booked_turns[idempotency_key] = replayed.model
        logger.info(
            "turn_replayed",
            extra={
                "role": self.role,
                "phase": self.phase,
                "project_id": self.project_id,
                "model": replayed.model,
            },
        )
        self._enforce_turn_limits()
        return replayed.result, replayed.usage

    def _step_saver(self) -> StepCheckpointSaver | None:
        if not self.thread_id or self.saver is None:
            return None
        if isinstance(self.saver, StepCheckpointSaver):
            return self.saver
        logger.warning(
            "tool idempotency_key ignored: %s does not implement StepCheckpointSaver",
            type(self.saver).__name__,
        )
        return None

    def _restore_tool_step(
        self, tool_name: str, idempotency_key: str | None
    ) -> _ReplayedTool | None:
        """Return the recorded result if this tool call already completed."""
        if not idempotency_key:
            return None
        saver = self._step_saver()
        if saver is None or self.thread_id is None:
            return None
        record = saver.get_step(
            self.thread_id, _tool_step_key(tool_name, idempotency_key)
        )
        if record is None:
            return None
        logger.info(
            "tool_replayed",
            extra={"role": self.role, "phase": self.phase, "tool": tool_name},
        )
        return _ReplayedTool(record.state.get("result"))

    def _persist_tool_step(
        self, tool_name: str, idempotency_key: str | None, result: Any
    ) -> None:
        """Record a completed tool call. JSON-serializable results only."""
        if not idempotency_key:
            return
        saver = self._step_saver()
        if saver is None or self.thread_id is None:
            return
        try:
            payload = json.loads(json.dumps(result))
        except (TypeError, ValueError):
            logger.warning(
                "tool step skipped: result of %s is not JSON-serializable, "
                "so a re-run will execute it again",
                tool_name,
            )
            return
        saver.put_step(
            self.thread_id,
            _tool_step_key(tool_name, idempotency_key),
            {"result": payload},
        )

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
        if self.worker.is_terminal:
            return
        self.worker.transition(AgentStatus.COMPLETED, detail=detail)
        self._emit_event(AgentEvent.completed(self.role, self.phase, detail=detail))

    def fail(
        self,
        detail: str,
        failure_class: AgentFailureClass = AgentFailureClass.UNKNOWN,
    ) -> None:
        """Mark the worker FAILED, unless it already reached a terminal state.

        An exception after ``handoff_to`` is logged but emits no ``agent.failed``:
        the worker completed, and the audit trail must not claim both.
        """
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
        if self.worker.is_terminal:
            return
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
        started: float | None = None,
    ) -> None:
        data: dict[str, Any] = {"tool": tool_name, "kind": kind}
        if started is not None:
            data["duration_seconds"] = time.monotonic() - started
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

    def _emit_guardrail_blocked(
        self, err: GuardrailViolatedError, *, started: float | None = None
    ) -> None:
        self._emit_tool_blocked(
            err.tool,
            kind="guardrail",
            stage=err.stage,
            guardrails=[violation.guardrail for violation in err.violations],
            started=started,
        )

    def _emit_tool_failed(
        self,
        tool_name: str,
        *,
        started: float,
        exc: BaseException,
        failure_class: AgentFailureClass = AgentFailureClass.TOOL_ERROR,
    ) -> None:
        self._emit_event(
            AgentEvent.failed(
                self.role,
                self.phase,
                failure_class,
                detail=f"tool execution failed: {tool_name}",
            ).with_data(
                {
                    "tool": tool_name,
                    "duration_seconds": time.monotonic() - started,
                    "error_type": type(exc).__name__,
                }
            )
        )

    def _emit_tool_completed(self, tool_name: str, *, started: float) -> None:
        self._emit_event(
            AgentEvent.tool_completed(self.role, self.phase, tool_name).with_data(
                {
                    "tool": tool_name,
                    "duration_seconds": time.monotonic() - started,
                }
            )
        )

    def _emit_turn_failed(
        self, model: str, *, started: float, exc: BaseException
    ) -> None:
        self._emit_event(
            AgentEvent.turn_failed(
                self.role,
                self.phase,
                model=model,
                duration_seconds=time.monotonic() - started,
                error_type=(
                    "cancelled"
                    if isinstance(exc, asyncio.CancelledError | GeneratorExit)
                    else type(exc).__name__
                ),
            )
        )

    def _begin_tool(
        self,
        tool_name: str,
        hook_ctx: HookContext | None,
        args: dict[str, Any] | None,
    ) -> HookContext:
        """Authorize and count a tool call, then resolve its hook context."""
        outcome = self.authorize(tool_name)
        if not outcome.allowed:
            self._emit_tool_blocked(tool_name, kind="permission")
            raise PermissionDeniedError(outcome)
        if self.governance is not None:
            known_args = args
            if known_args is None and hook_ctx is not None and hook_ctx.args:
                known_args = hook_ctx.args
            self._check_governance_pre_tool(
                None
                if known_args is None
                else safe_tool_call_signature(tool_name, known_args)
            )
        return self._resolve_hook_ctx(hook_ctx, tool=tool_name, args=args)

    def _next_fallback_model(
        self,
        exc: Exception,
        *,
        current: str,
        tried: Sequence[str],
        failover_on: frozenset[FailureScenario],
    ) -> str | None:
        """Return the provider to try next, or None if ``exc`` must propagate."""
        # Identity, not type: an exception from a hook or the saver can look just
        # like a provider failure, but the provider has already answered and been
        # paid. Only what the model call itself raised may switch provider.
        if self.provider_router is None or exc is not self._last_model_call_error:
            return None
        self._last_model_call_error = None
        if isinstance(exc, _NO_FAILOVER_ERRORS):
            return None
        scenario = classify_exception(exc)
        if scenario not in failover_on:
            return None
        candidate = self.provider_router.select(
            scenario=scenario,
            attempt=len(tried),
            current=current,
            exclude=tuple(tried),
        )
        if candidate is None or candidate in tried:
            return None
        self._emit_event(
            AgentEvent.recovery_attempted(
                self.role,
                self.phase,
                detail=f"{scenario.value}: failover {current} -> {candidate}",
            )
        )
        return candidate

    def _check_governance_pre_turn(self) -> None:
        """Tick the turn counter and enforce limits before fn runs."""
        if self.governance is None:
            return
        self.governance.state.record_turn_start()
        self._enforce_governance()

    def _check_governance_pre_tool(self, signature: str | None = None) -> None:
        """Tick the tool counter and enforce limits before tool fn runs."""
        if self.governance is None:
            return
        self.governance.state.record_tool_call(signature)
        self._enforce_governance()
        # Survived this check, so a later turn must not re-alert on this call. A
        # terminate breach raised above and stays visible to every later check.
        self.governance.state.mark_tool_call_evaluated()

    def _record_governance_outcome(
        self, *, success: bool, cost_usd: float | None = None
    ) -> None:
        """Update success/failure streak + cost. Never raises a breach."""
        if self.governance is None:
            return
        if success:
            self.governance.state.record_success()
        else:
            self.governance.state.record_failure()
        if cost_usd is not None:
            self.governance.state.record_cost(cost_usd)

    def _record_governance_turn_outcome(
        self, *, success: bool, cost_usd: float | None = None
    ) -> None:
        """Record the outcome, then surface any cost or failure-streak breach."""
        self._record_governance_outcome(success=success, cost_usd=cost_usd)
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

    def _record_turn_usage(self, model: str, snapshot: UsageSnapshot) -> float:
        """Add a turn's usage to the tracker and sink; return its cost.

        A turn with no ``model`` still counts towards token limits. It has no
        price, and sinks need a model name to label the record, so it stops at
        the tracker.
        """
        self.tracker.record_turn(model, snapshot)
        cost = self.tracker.cost_for_turn(model, snapshot)
        if model:
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
        return cost

    def _settle_turn(
        self,
        *,
        result: Any,
        snapshot: UsageSnapshot,
        model: str,
        idempotency_key: str | None,
        started: float,
    ) -> None:
        """Book a finished model call, before any limit may reject it.

        The call has already been paid for, so this runs before any limit: a limit
        raising afterwards can neither hide the spend from later checks nor make a
        retry of the same idempotency key pay twice. The checkpoint goes last
        because it is the one step that can fail on outside trouble, and a saver
        outage must not hide the spend either.
        """
        cost = self._record_turn_usage(model, snapshot)
        if idempotency_key:
            self._booked_turns[idempotency_key] = model
        self._record_governance_outcome(success=True, cost_usd=cost)
        self._emit_event(
            AgentEvent.turn_completed(
                self.role,
                self.phase,
                model=model,
                duration_seconds=time.monotonic() - started,
                data={
                    "input_tokens": snapshot.input_tokens,
                    "output_tokens": snapshot.output_tokens,
                    "cost_usd": cost,
                },
            )
        )
        self._persist_turn_checkpoint(
            result=result,
            usage=snapshot,
            model=model,
            idempotency_key=idempotency_key,
        )

    def _account_unfinished_turn(
        self,
        model: str,
        output: Any,
        usage: UsageSnapshot | None,
        estimate_usage: Callable[[Any], UsageSnapshot] | None,
        *,
        failed: bool = True,
    ) -> None:
        """Book a call that was paid for but will not return a result.

        Covers a failing or cancelled ``after_model`` hook and a stream that ended
        early. It runs while another exception is on its way out, so it never
        raises: limits see the spend on the next pre-turn check. ``failed=False``
        is for cancellation, which is the caller's choice rather than a failure
        and must not feed a failure streak.
        """
        cost: float | None = None
        try:
            if usage is not None or estimate_usage is not None:
                snapshot = self._resolve_usage(output, usage, estimate_usage)
                cost = self._record_turn_usage(model, snapshot)
        except Exception as exc:
            logger.error(
                "could not account for an unfinished turn",
                extra={
                    "role": self.role,
                    "phase": self.phase,
                    "project_id": self.project_id,
                    "error_type": type(exc).__name__,
                },
            )
        if failed:
            self._record_governance_outcome(success=False, cost_usd=cost)
        elif cost is not None and self.governance is not None:
            self.governance.state.record_cost(cost)

    def _account_unfinished_stream(
        self,
        model: str,
        chunks: list[str],
        usage: UsageSnapshot | None,
        estimate_usage: Callable[[Any], UsageSnapshot] | None,
        *,
        failed: bool = True,
    ) -> None:
        """Book a stream that ended early, charging only if text arrived.

        A connection that fails before the first chunk was not charged, which
        matches what ``arun_turn`` books for the same failure.
        """
        self._account_unfinished_turn(
            model,
            "".join(chunks),
            usage if chunks else None,
            estimate_usage if chunks else None,
            failed=failed,
        )

    def _check_limits_pre_turn(self) -> None:
        """Refuse a live turn once booked usage is already over a hard limit.

        A turn that ended early books its spend without raising, so this is what
        stops the next one before it pays.
        """
        if self.enforce_budget:
            self._enforce_budget()
        self._enforce_usage_limits()

    def _enforce_turn_limits(self) -> None:
        """Raise if the booked usage crossed a limit.

        Governance goes first: its breach is terminal, while a budget error is
        recoverable and would otherwise hide the breach until the next turn.
        """
        self._enforce_governance()
        self._enforce_budget()
        self._enforce_usage_limits()

    def _enforce_budget(self) -> None:
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

    def _enforce_usage_limits(self) -> None:
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
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
    ) -> T:
        """Execute a tool with permission + guardrail + governance + hook checks.

        Order: permission → governance → replay lookup → before_tool hooks →
        pre-guardrails → fn() → post-guardrails → after_tool hooks.

        ``hook_ctx`` is a mutable context shared with the hook chain. When
        omitted the runtime synthesizes a fresh ``HookContext`` so hooks still
        fire — but any mutation hooks attempt on ``ctx.args`` is lost because
        the caller's closure does not see it. Pass a context (and close over
        ``ctx.args`` inside ``fn``) when you want hooks to redact or rewrite
        tool inputs.

        ``args`` describes the call to hooks and to
        ``MaxRepeatedToolCallsLimit``; ``fn`` still takes no arguments.

        ``timeout`` raises :class:`ToolTimeoutError`. The worker thread cannot
        be killed, so a timed-out ``fn`` finishes in the background.

        With a ``saver`` and ``thread_id`` configured, ``idempotency_key``
        records the result once the call passes its checks and replays it on a
        re-run instead of repeating the side effect. A crash between ``fn``
        returning and the record being written still re-executes it, so the
        guarantee is at-least-once: pair it with an idempotent tool or pass the
        same key to the downstream API.
        """
        ctx = self._begin_tool(tool_name, hook_ctx, args)
        replayed = self._restore_tool_step(tool_name, idempotency_key)
        if replayed is not None:
            cached: T = replayed.result
            return cached
        run_before_tool(self.hooks, ctx)
        try:
            run_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc)
            raise
        self._emit_event(AgentEvent.tool_called(self.role, self.phase, tool_name))
        started = time.monotonic()
        try:
            result_raw: T = (
                fn()
                if timeout is None
                else _call_with_deadline(
                    fn, timeout, lambda: ToolTimeoutError(tool_name, timeout)
                )
            )
        except Exception as exc:
            self._emit_tool_failed(tool_name, started=started, exc=exc)
            raise
        try:
            run_post_checks(self.guardrails, result_raw, role=self.role, tool=tool_name)
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc, started=started)
            raise
        try:
            result: T = run_after_tool(self.hooks, ctx, result_raw)
        except Exception as exc:
            self._emit_tool_failed(tool_name, started=started, exc=exc)
            raise
        self._persist_tool_step(tool_name, idempotency_key, result)
        self._emit_tool_completed(tool_name, started=started)
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
        ``fn``, and counts that usage towards this session's limits.
        After a successful execution, a new checkpoint is written so a
        restart can replay through it. A turn that crosses a budget is
        checkpointed before the budget error is raised, because it was
        already paid for.
        """
        replayed = self._restore_idempotent_turn(idempotency_key)
        # Iteration count ticks on a replay too, so the cap reflects logical
        # progress through the loop, not just live executions.
        self._check_iteration_cap()
        self._check_governance_pre_turn()
        if replayed is not None:
            return self._replay_turn(replayed, idempotency_key)

        self._check_limits_pre_turn()
        ctx = self._resolve_hook_ctx(hook_ctx, model=model)
        # Hooks run BEFORE the model call so they can mutate ctx.prompt
        # in place. Caller's fn() must close over ctx.prompt to see the
        # mutated value — see docs/patterns/hooks.md for the pattern.
        run_before_model(self.hooks, ctx)
        if self.rate_limiter is not None:
            self.rate_limiter.acquire_pre_call()
        started = time.monotonic()
        try:
            raw = self._invoke_fn(fn, timeout=timeout)
        except CircuitOpenError as exc:
            self._emit_turn_failed(model, started=started, exc=exc)
            self._record_governance_turn_outcome(success=False)
            raise
        except Exception as exc:
            self._last_model_call_error = exc
            self._emit_turn_failed(model, started=started, exc=exc)
            _record_recovery_event(self, exc)
            self._record_governance_turn_outcome(success=False)
            raise

        try:
            result = run_after_model(self.hooks, ctx, raw)
            snapshot = self._resolve_usage(result, usage, estimate_usage)
        except Exception as exc:
            self._emit_turn_failed(model, started=started, exc=exc)
            self._account_unfinished_turn(model, raw, usage, estimate_usage)
            raise
        self._settle_turn(
            result=result,
            snapshot=snapshot,
            model=model,
            idempotency_key=idempotency_key,
            started=started,
        )
        if self.rate_limiter is not None:
            self.rate_limiter.acquire_usage(
                input_tokens=snapshot.input_tokens,
                output_tokens=snapshot.output_tokens,
            )
        self._enforce_turn_limits()
        return result, snapshot

    def run_turn_with_fallback(
        self,
        fn: Callable[[str], T],
        *,
        model: str,
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[T], UsageSnapshot] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
        hook_ctx: HookContext | None = None,
        failover_on: frozenset[FailureScenario] = DEFAULT_FAILOVER_SCENARIOS,
    ) -> tuple[T, UsageSnapshot, str]:
        """Run a turn, asking ``provider_router`` for another model on failure.

        ``fn`` receives the model to call. Returns ``(result, usage, model)``,
        where ``model`` is the one that answered. Each attempt is a full turn:
        it counts towards ``max_iterations`` and governance, so those still bound
        the total. Only failures in ``failover_on`` switch provider. A governance
        breach, budget or usage limit, permission denial, or open circuit always
        propagates, as does the last failure once the router has no candidate.

        A circuit breaker on the session is shared by every model, so it should
        guard something they have in common, such as a gateway.
        """
        if self.provider_router is None:
            raise ValueError("run_turn_with_fallback requires a provider_router")
        current = model
        tried = [current]
        initial_prompt = (
            copy.deepcopy(hook_ctx.prompt) if hook_ctx is not None else None
        )
        initial_args = copy.deepcopy(hook_ctx.args) if hook_ctx is not None else {}
        while True:
            candidate = current
            try:
                result, snapshot = self.run_turn(
                    functools.partial(fn, candidate),
                    model=candidate,
                    usage=usage,
                    estimate_usage=estimate_usage,
                    timeout=timeout,
                    idempotency_key=idempotency_key,
                    hook_ctx=hook_ctx,
                )
            except Exception as exc:
                following = self._next_fallback_model(
                    exc, current=current, tried=tried, failover_on=failover_on
                )
                if following is None:
                    raise
                tried.append(following)
                current = following
                if hook_ctx is not None:
                    # Hooks rewrite the context, and each attempt runs them anew.
                    hook_ctx.prompt = copy.deepcopy(initial_prompt)
                    hook_ctx.args = copy.deepcopy(initial_args)
                continue
            answered = current
            if idempotency_key:
                answered = self._booked_turns.get(idempotency_key, current)
            return result, snapshot, answered

    def _invoke_fn(self, fn: Callable[[], T], *, timeout: float | None) -> T:
        if timeout is None:
            return self._wrapped_call(fn)
        # The breaker wraps the call inside the worker thread, so it counts a
        # downstream exception but not the deadline itself: a hard timeout is
        # a session-level TurnTimeoutError, not a downstream signal.
        return _call_with_deadline(
            lambda: self._wrapped_call(fn), timeout, lambda: TurnTimeoutError(timeout)
        )

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
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
    ) -> T:
        """Async sibling of run_tool.

        Permission + governance are sync; guardrails are awaited when
        they implement ``AsyncGuardrail`` and called inline when they
        are sync ``Guardrail`` instances. Hooks are dispatched both
        sync and async — see ``hooks.py``.

        ``args``, ``timeout`` and ``idempotency_key`` work as in ``run_tool``,
        except that a timed-out tool is cancelled rather than left running.
        """
        ctx = self._begin_tool(tool_name, hook_ctx, args)
        replayed = self._restore_tool_step(tool_name, idempotency_key)
        if replayed is not None:
            cached: T = replayed.result
            return cached
        await arun_before_tool(self.hooks, ctx)
        try:
            await arun_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc)
            raise
        self._emit_event(AgentEvent.tool_called(self.role, self.phase, tool_name))
        started = time.monotonic()
        try:
            if timeout is None:
                result_raw: T = await coro_factory()
            else:
                scope = asyncio.timeout(timeout)
                try:
                    async with scope:
                        result_raw = await coro_factory()
                except TimeoutError as exc:
                    if scope.expired():
                        raise ToolTimeoutError(tool_name, timeout) from exc
                    raise
        except asyncio.CancelledError as exc:
            self._emit_tool_failed(
                tool_name,
                started=started,
                exc=exc,
                failure_class=AgentFailureClass.CANCELLED,
            )
            raise
        except Exception as exc:
            self._emit_tool_failed(tool_name, started=started, exc=exc)
            raise
        try:
            await arun_post_checks(
                self.guardrails, result_raw, role=self.role, tool=tool_name
            )
        except GuardrailViolatedError as exc:
            self._emit_guardrail_blocked(exc, started=started)
            raise
        try:
            result: T = await arun_after_tool(self.hooks, ctx, result_raw)
        except Exception as exc:
            self._emit_tool_failed(tool_name, started=started, exc=exc)
            raise
        self._persist_tool_step(tool_name, idempotency_key, result)
        self._emit_tool_completed(tool_name, started=started)
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

        ``timeout`` is enforced with ``asyncio.timeout``. Cancellation
        from outside (parent task) is propagated as CancelledError; an
        internal timeout becomes ``TurnTimeoutError``.

        ``idempotency_key`` behaves the same as in ``run_turn``: when the
        session has a saver + thread_id configured, a prior checkpoint
        with the same key short-circuits the call and counts its usage.
        """
        replayed = self._restore_idempotent_turn(idempotency_key)
        self._check_iteration_cap()
        self._check_governance_pre_turn()
        if replayed is not None:
            return self._replay_turn(replayed, idempotency_key)

        self._check_limits_pre_turn()
        ctx = self._resolve_hook_ctx(hook_ctx, model=model)
        await arun_before_model(self.hooks, ctx)
        if self.async_rate_limiter is not None:
            await self.async_rate_limiter.acquire_pre_call()
        started = time.monotonic()
        try:
            raw = await self._ainvoke(coro_factory, timeout=timeout)
        except CircuitOpenError as exc:
            self._emit_turn_failed(model, started=started, exc=exc)
            self._record_governance_turn_outcome(success=False)
            raise
        except asyncio.CancelledError as exc:
            self._emit_turn_failed(model, started=started, exc=exc)
            raise
        except Exception as exc:
            self._last_model_call_error = exc
            self._emit_turn_failed(model, started=started, exc=exc)
            await _arecord_recovery_event(self, exc)
            self._record_governance_turn_outcome(success=False)
            raise

        try:
            result = await arun_after_model(self.hooks, ctx, raw)
            snapshot = self._resolve_usage(result, usage, estimate_usage)
        except (Exception, asyncio.CancelledError) as exc:
            self._emit_turn_failed(model, started=started, exc=exc)
            self._account_unfinished_turn(
                model,
                raw,
                usage,
                estimate_usage,
                failed=not isinstance(exc, asyncio.CancelledError),
            )
            raise
        self._settle_turn(
            result=result,
            snapshot=snapshot,
            model=model,
            idempotency_key=idempotency_key,
            started=started,
        )
        if self.async_rate_limiter is not None:
            await self.async_rate_limiter.acquire_usage(
                input_tokens=snapshot.input_tokens,
                output_tokens=snapshot.output_tokens,
            )
        self._enforce_turn_limits()
        return result, snapshot

    async def arun_turn_with_fallback(
        self,
        coro_factory: Callable[[str], Awaitable[T]],
        *,
        model: str,
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[T], UsageSnapshot] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
        hook_ctx: HookContext | None = None,
        failover_on: frozenset[FailureScenario] = DEFAULT_FAILOVER_SCENARIOS,
    ) -> tuple[T, UsageSnapshot, str]:
        """Async sibling of ``run_turn_with_fallback``."""
        if self.provider_router is None:
            raise ValueError("arun_turn_with_fallback requires a provider_router")
        current = model
        tried = [current]
        initial_prompt = (
            copy.deepcopy(hook_ctx.prompt) if hook_ctx is not None else None
        )
        initial_args = copy.deepcopy(hook_ctx.args) if hook_ctx is not None else {}
        while True:
            candidate = current
            try:
                result, snapshot = await self.arun_turn(
                    functools.partial(coro_factory, candidate),
                    model=candidate,
                    usage=usage,
                    estimate_usage=estimate_usage,
                    timeout=timeout,
                    idempotency_key=idempotency_key,
                    hook_ctx=hook_ctx,
                )
            except Exception as exc:
                following = self._next_fallback_model(
                    exc, current=current, tried=tried, failover_on=failover_on
                )
                if following is None:
                    raise
                tried.append(following)
                current = following
                if hook_ctx is not None:
                    # Hooks rewrite the context, and each attempt runs them anew.
                    hook_ctx.prompt = copy.deepcopy(initial_prompt)
                    hook_ctx.args = copy.deepcopy(initial_args)
                continue
            answered = current
            if idempotency_key:
                answered = self._booked_turns.get(idempotency_key, current)
            return result, snapshot, answered

    async def arun_turn_stream(
        self,
        chunk_factory: Callable[[], AsyncIterator[str]],
        *,
        model: str = "",
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[str], UsageSnapshot] | None = None,
        timeout: float | None = None,
        idempotency_key: str | None = None,
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

        ``timeout`` is a deadline for the whole stream, checked only while
        waiting on upstream, so it can never fire inside the consumer's own code.
        A consumer that is slow between chunks hits it on the next pull.

        ``idempotency_key`` replays a recorded turn as one text event plus the
        terminal event, without calling upstream.

        Cancellation: wrap the generator in ``contextlib.aclosing``. When the
        consumer leaves the block early the upstream iterator's ``aclose()`` is
        awaited and ``session._last_stream_cancelled`` flips to ``True``. A plain
        ``break`` without ``aclosing`` defers all of that to garbage collection.
        No ``final("cancelled")`` event is emitted because the consumer is no
        longer listening; check the flag instead.

        A stream that ends early books its usage when ``usage`` or
        ``estimate_usage`` can provide it (the latter sees the partial text),
        because the provider charges for what it already sent.

        On failure, including a limit crossed once the stream has finished,
        ``error`` + ``final("failed")`` events are yielded and the exception is
        re-raised so callers see it.
        """
        self._last_stream_cancelled = False
        replayed = self._restore_idempotent_turn(idempotency_key)
        self._check_iteration_cap()
        self._check_governance_pre_turn()
        if replayed is not None:
            try:
                replay_result, replay_usage = self._replay_turn(
                    replayed, idempotency_key
                )
            except Exception as exc:
                safe_detail = _safe_exception_detail(exc)
                yield StreamEvent.error(type(exc).__name__, safe_detail)
                yield StreamEvent.final("failed", detail=safe_detail)
                raise
            if replay_result is not None:
                yield StreamEvent.text(str(replay_result))
            yield StreamEvent.final("completed", usage=replay_usage.to_dict())
            return

        self._check_limits_pre_turn()
        ctx = self._resolve_hook_ctx(hook_ctx, model=model)
        await arun_before_model(self.hooks, ctx)

        if self.async_rate_limiter is not None:
            await self.async_rate_limiter.acquire_pre_call()

        started = time.monotonic()
        aggregated: list[str] = []
        upstream: AsyncIterator[str] | None = None
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout

        try:
            try:
                upstream = chunk_factory().__aiter__()
                # PEP 789: a yield inside an asyncio.timeout block would deliver
                # the cancellation to the consumer's task. Only the pull is
                # scoped, and the yield stays outside it.
                while True:
                    try:
                        chunk = await self._next_chunk(upstream, deadline, timeout)
                    except StopAsyncIteration:
                        break
                    if not isinstance(chunk, str):
                        raise TypeError(
                            f"stream chunk must be str, got {type(chunk).__name__}"
                        )
                    aggregated.append(chunk)
                    yield StreamEvent.text(chunk)
            except TurnTimeoutError as exc:
                # Record the failure outcome BEFORE yielding the terminal
                # event: an idiomatic consumer that breaks the async-for on
                # seeing ``final`` would otherwise throw GeneratorExit at that
                # yield and skip the recovery/governance recording entirely.
                self._emit_turn_failed(model, started=started, exc=exc)
                await _arecord_recovery_event(self, exc)
                self._account_unfinished_stream(
                    model, aggregated, usage, estimate_usage
                )
                yield StreamEvent.error("timeout", f"stream exceeded {timeout}s")
                yield StreamEvent.final("failed", detail="timeout")
                self._enforce_governance()
                raise
            except (GeneratorExit, asyncio.CancelledError) as exc:
                # The consumer left or the task was cancelled. Nothing may be
                # yielded, and a breach must not replace the cancellation: the
                # next pre-turn check reports it instead.
                self._last_stream_cancelled = True
                self._emit_turn_failed(model, started=started, exc=exc)
                self._account_unfinished_stream(
                    model, aggregated, usage, estimate_usage, failed=False
                )
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
            except Exception as exc:
                safe_detail = _safe_exception_detail(exc)
                # Record before the terminal yield (see the timeout branch).
                self._emit_turn_failed(model, started=started, exc=exc)
                await _arecord_recovery_event(self, exc)
                self._account_unfinished_stream(
                    model, aggregated, usage, estimate_usage
                )
                yield StreamEvent.error(type(exc).__name__, safe_detail)
                yield StreamEvent.final("failed", detail=safe_detail)
                self._enforce_governance()
                raise

            full_text = "".join(aggregated)
            try:
                transformed = await arun_after_model(self.hooks, ctx, full_text)
                snapshot = self._resolve_usage(transformed, usage, estimate_usage)
            except (Exception, asyncio.CancelledError) as exc:
                cancelled = isinstance(exc, asyncio.CancelledError)
                self._emit_turn_failed(model, started=started, exc=exc)
                self._account_unfinished_turn(
                    model, full_text, usage, estimate_usage, failed=not cancelled
                )
                if isinstance(exc, asyncio.CancelledError):
                    raise
                safe_detail = _safe_exception_detail(exc)
                yield StreamEvent.error(type(exc).__name__, safe_detail)
                yield StreamEvent.final("failed", detail=safe_detail)
                raise
            try:
                self._settle_turn(
                    result=transformed,
                    snapshot=snapshot,
                    model=model,
                    idempotency_key=idempotency_key,
                    started=started,
                )
                if self.async_rate_limiter is not None:
                    await self.async_rate_limiter.acquire_usage(
                        input_tokens=snapshot.input_tokens,
                        output_tokens=snapshot.output_tokens,
                    )
                self._enforce_turn_limits()
            except Exception as exc:
                safe_detail = _safe_exception_detail(exc)
                yield StreamEvent.error(type(exc).__name__, safe_detail)
                yield StreamEvent.final(
                    "failed", detail=safe_detail, usage=snapshot.to_dict()
                )
                raise
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
                        if timeout is None:
                            await aclose()
                        else:
                            # The caller asked for a bounded stream, so an upstream
                            # that hangs while closing must not outlive it for good.
                            async with asyncio.timeout(_STREAM_CLOSE_GRACE_SECONDS):
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

    @staticmethod
    async def _next_chunk(
        upstream: AsyncIterator[str],
        deadline: float | None,
        timeout: float | None,
    ) -> str:
        """Pull one chunk, turning an expired deadline into TurnTimeoutError."""
        if deadline is None or timeout is None:
            return await anext(upstream)
        if asyncio.get_running_loop().time() >= deadline:
            raise TurnTimeoutError(timeout)
        scope = asyncio.timeout_at(deadline)
        try:
            async with scope:
                return await anext(upstream)
        except TimeoutError as exc:
            # Only our own deadline is a turn timeout. An upstream's TimeoutError
            # is an ordinary upstream failure.
            if scope.expired():
                raise TurnTimeoutError(timeout) from exc
            raise

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
        scope = asyncio.timeout(timeout)
        try:
            async with scope:
                return await self._wrapped_acall(coro_factory)
        except TimeoutError as exc:
            if scope.expired():
                raise TurnTimeoutError(timeout) from exc
            raise

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

        This method only provides the concurrency. It applies no permission,
        guardrail, governance or hook checks, so have each factory go through
        ``arun_tool``: ``lambda: session.arun_tool("search", search_factory)``.
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
        except asyncio.CancelledError:
            # A timed-out approval wait is normal control flow. Leave the worker
            # runnable, so the session can continue or exit cleanly; a real
            # cancellation still reaches asession(), which marks it CANCELLED.
            if not self.worker.is_terminal:
                self.worker.transition(AgentStatus.RUNNING, detail="input wait ended")
                self._emit_event(
                    AgentEvent.ready(
                        self.role,
                        self.phase,
                        detail="input wait ended",
                        data={"kind": "human_input"},
                    )
                )
            raise
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


# Raised by the runtime's own limits. Another provider would not change the answer.
_NO_FAILOVER_ERRORS = (
    GovernanceBreachError,
    MaxIterationsExceededError,
    BudgetExceededError,
    UsageLimitExceededError,
    RateLimitExceededError,
    PermissionDeniedError,
    CircuitOpenError,
)


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
    # ToolTimeoutError and MCPToolError name the tool that failed. Duck-typed so
    # this module need not import the optional MCP integration; classifying by
    # type also keeps an MCP server's error text from steering the failure class.
    if isinstance(getattr(exc, "tool_name", None), str):
        return AgentFailureClass.TOOL_ERROR
    scenario = classify_exception(exc)
    if scenario != FailureScenario.LLM_ERROR:
        return _scenario_to_class(scenario)
    if _is_prompt_rejection_exception(exc):
        return AgentFailureClass.PROMPT_REJECTION
    if isinstance(exc, ValueError | TypeError):
        return AgentFailureClass.VALIDATION_ERROR
    return _scenario_to_class(scenario)


__all__ = [
    "AgentSession",
    "AsyncOrchestrationSession",
    "DEFAULT_FAILOVER_SCENARIOS",
    "MaxIterationsExceededError",
    "OrchestrationSession",
    "PermissionDeniedError",
    "ToolTimeoutError",
    "TurnTimeoutError",
]
