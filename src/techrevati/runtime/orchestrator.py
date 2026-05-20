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
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

from techrevati.runtime.agent_events import AgentEvent, AgentFailureClass
from techrevati.runtime.agent_lifecycle import (
    AgentRegistry,
    AgentStatus,
    AgentWorker,
)
from techrevati.runtime.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitBreaker,
    CircuitOpenError,
)
from techrevati.runtime.guardrails import (
    Guardrail,
    run_post_checks,
    run_pre_checks,
)
from techrevati.runtime.handoffs import Handoff
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
    aattempt_recovery,
    attempt_recovery,
    classify_exception,
)
from techrevati.runtime.sinks import (
    EventSink,
    NoopEventSink,
    NoopUsageSink,
    UsageSink,
)
from techrevati.runtime.usage_tracking import (
    BudgetExceededError,
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
    runaway agent loops — Anthropic explicitly names stopping conditions as a
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
class Orchestrator:
    """Factory for sessions. Holds shared, long-lived components.

    Components are optional; the simplest invocation is
    ``Orchestrator(role=..., phase=...)``. Provide ``circuit_breaker``
    for sync sessions, ``async_circuit_breaker`` for async sessions, or
    both — they are independent.
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
    guardrails: list[Guardrail] = field(default_factory=list)
    event_sink: EventSink = field(default_factory=NoopEventSink)
    usage_sink: UsageSink = field(default_factory=NoopUsageSink)

    @contextmanager
    def session(self) -> Iterator[OrchestrationSession]:
        """Open a single-agent sync session.

        On clean exit: worker → COMPLETED if still running. On exception:
        worker → FAILED with the error classified into an AgentFailureClass.
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

    @asynccontextmanager
    async def asession(self) -> AsyncIterator[AsyncOrchestrationSession]:
        """Open a single-agent async session.

        Mirrors ``session()`` but uses async primitives. ``CancelledError``
        from anywhere inside the ``async with`` body transitions the
        worker to CANCELLED instead of FAILED, and is re-raised.
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
        )

        try:
            yield session
        except asyncio.CancelledError:
            session.cancel(detail="async session cancelled")
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
    guardrails: list[Guardrail] = field(default_factory=list)
    event_sink: EventSink = field(default_factory=NoopEventSink)
    usage_sink: UsageSink = field(default_factory=NoopUsageSink)
    events: list[AgentEvent] = field(default_factory=list)
    _started_at: float = field(default_factory=time.monotonic, init=False, repr=False)
    _iteration_count: int = field(default=0, init=False, repr=False)

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


@dataclass
class OrchestrationSession(_SessionBase):
    """Single-agent sync execution context. Created by Orchestrator.session()."""

    circuit_breaker: CircuitBreaker | None = None

    def run_tool(
        self,
        tool_name: str,
        fn: Callable[[], T],
    ) -> T:
        """Execute a tool with permission + guardrail checks around it.

        Order: permission → pre-guardrails → fn() → post-guardrails.
        """
        outcome = self.authorize(tool_name)
        if not outcome.allowed:
            raise PermissionDeniedError(outcome)
        run_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        result = fn()
        run_post_checks(self.guardrails, result, role=self.role, tool=tool_name)
        return result

    def run_turn(
        self,
        fn: Callable[[], T],
        model: str = "",
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[T], UsageSnapshot] | None = None,
        timeout: float | None = None,
    ) -> tuple[T, UsageSnapshot]:
        """Execute one model turn with circuit-breaker + recovery wiring.

        When ``timeout`` is set, ``fn`` is dispatched to a single-worker
        ``ThreadPoolExecutor`` and waited on with that deadline. The
        executor is created per-turn (cheap) so there is no pool to
        manage across the session.

        On exception: classifies the failure, attempts recovery once,
        and re-raises so the caller decides whether to retry.
        """
        self._check_iteration_cap()
        try:
            result = self._invoke_fn(fn, timeout=timeout)
        except CircuitOpenError:
            raise
        except TurnTimeoutError:
            raise
        except Exception as exc:
            _record_recovery_event(self, exc)
            raise

        snapshot = self._resolve_usage(result, usage, estimate_usage)
        self._apply_usage_and_check_budget(model, snapshot)
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
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(self._wrapped_call, fn)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise TurnTimeoutError(timeout) from exc

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

    async def arun_tool(
        self,
        tool_name: str,
        coro_factory: Callable[[], Awaitable[T]],
    ) -> T:
        """Async sibling of run_tool.

        Permission + guardrails are sync; the call itself is awaited.
        """
        outcome = self.authorize(tool_name)
        if not outcome.allowed:
            raise PermissionDeniedError(outcome)
        run_pre_checks(self.guardrails, role=self.role, tool=tool_name)
        result = await coro_factory()
        run_post_checks(self.guardrails, result, role=self.role, tool=tool_name)
        return result

    async def arun_turn(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        model: str = "",
        usage: UsageSnapshot | None = None,
        estimate_usage: Callable[[T], UsageSnapshot] | None = None,
        timeout: float | None = None,
    ) -> tuple[T, UsageSnapshot]:
        """Execute one model turn with async circuit-breaker + recovery wiring.

        ``timeout`` is enforced with ``asyncio.wait_for``. Cancellation
        from outside (parent task) is propagated as CancelledError; an
        internal timeout becomes ``TurnTimeoutError``.
        """
        self._check_iteration_cap()
        try:
            result = await self._ainvoke(coro_factory, timeout=timeout)
        except CircuitOpenError:
            raise
        except TurnTimeoutError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _arecord_recovery_event(self, exc)
            raise

        snapshot = self._resolve_usage(result, usage, estimate_usage)
        self._apply_usage_and_check_budget(model, snapshot)
        return result, snapshot

    async def _ainvoke(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        *,
        timeout: float | None,
    ) -> T:
        if timeout is None:
            return await self._wrapped_acall(coro_factory)

        try:
            return await asyncio.wait_for(
                self._wrapped_acall(coro_factory),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise TurnTimeoutError(timeout) from exc

    async def _wrapped_acall(self, coro_factory: Callable[[], Awaitable[T]]) -> T:
        if self.circuit_breaker is not None:
            return await self.circuit_breaker.call(coro_factory)
        return await coro_factory()

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


# Forward-looking name. 0.2.0 will promote ``AgentSession`` to the canonical
# class name with ``Orchestrator`` kept as a deprecation alias. Adding the
# alias now lets new code adopt the future name while existing code keeps
# working unchanged.
AgentSession = Orchestrator


__all__ = [
    "AgentSession",
    "AsyncOrchestrationSession",
    "MaxIterationsExceededError",
    "OrchestrationSession",
    "Orchestrator",
    "PermissionDeniedError",
    "TurnTimeoutError",
]
