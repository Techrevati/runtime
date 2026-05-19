"""
Agent Events — Typed lifecycle events with a failure taxonomy.

Provides a structured event schema for agent execution. Events are
JSON-serializable and include both an 'event' (full path) and 'type'
(short tail) field so they can be routed by either consumers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class AgentEventName(str, Enum):
    """Typed event names for agent and phase lifecycle."""

    # Agent lifecycle
    AGENT_STARTED = "agent.started"
    AGENT_READY = "agent.ready"
    AGENT_BLOCKED = "agent.blocked"
    AGENT_TOOL_CALLED = "agent.tool_called"
    AGENT_TOOL_COMPLETED = "agent.tool_completed"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    # Phase lifecycle
    PHASE_STARTED = "phase.started"
    PHASE_COMPLETED = "phase.completed"
    PHASE_GATE_EVALUATED = "phase.gate_evaluated"
    PHASE_GATE_PASSED = "phase.gate_passed"
    PHASE_GATE_FAILED = "phase.gate_failed"
    # Recovery
    RECOVERY_ATTEMPTED = "agent.recovery.attempted"
    RECOVERY_SUCCEEDED = "agent.recovery.succeeded"
    RECOVERY_FAILED = "agent.recovery.failed"
    RECOVERY_ESCALATED = "agent.recovery.escalated"
    RECOVERY_PROVIDER_SWITCHED = "agent.recovery.provider_switched"
    # Hooks
    HOOK_PRE_TOOL = "hook.pre_tool"
    HOOK_POST_TOOL = "hook.post_tool"


class AgentEventStatus(str, Enum):
    """Current status at time of event emission."""

    RUNNING = "running"
    READY = "ready"
    BLOCKED = "blocked"
    GREEN = "green"
    RED = "red"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentFailureClass(str, Enum):
    """Failure taxonomy for structured error classification."""

    LLM_TIMEOUT = "llm_timeout"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    DEPENDENCY_FAILED = "dependency_failed"
    MEMORY_CORRUPTION = "memory_corruption"
    VALIDATION_ERROR = "validation_error"
    PROMPT_REJECTION = "prompt_rejection"
    UNKNOWN = "unknown"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class AgentEvent:
    """A typed lifecycle event for agent orchestration."""

    event: AgentEventName
    status: AgentEventStatus
    emitted_at: str = field(default_factory=_now_iso)
    role: str | None = None
    phase: str | None = None
    project_id: int | None = None
    failure_class: AgentFailureClass | None = None
    detail: str | None = None
    data: dict[str, Any] | None = None

    # -- Builder methods (return new instances) --

    def with_failure_class(self, fc: AgentFailureClass) -> AgentEvent:
        return replace(self, failure_class=fc)

    def with_detail(self, detail: str) -> AgentEvent:
        return replace(self, detail=detail)

    def with_data(self, data: dict[str, Any]) -> AgentEvent:
        return replace(self, data=data)

    def with_project(self, project_id: int) -> AgentEvent:
        return replace(self, project_id=project_id)

    # -- Convenience constructors --

    @classmethod
    def started(cls, role: str, phase: str) -> AgentEvent:
        return cls(
            event=AgentEventName.AGENT_STARTED,
            status=AgentEventStatus.RUNNING,
            role=role,
            phase=phase,
        )

    @classmethod
    def completed(cls, role: str, phase: str, detail: str | None = None) -> AgentEvent:
        return cls(
            event=AgentEventName.AGENT_COMPLETED,
            status=AgentEventStatus.COMPLETED,
            role=role,
            phase=phase,
            detail=detail,
        )

    @classmethod
    def failed(
        cls,
        role: str,
        phase: str,
        failure_class: AgentFailureClass,
        detail: str | None = None,
    ) -> AgentEvent:
        return cls(
            event=AgentEventName.AGENT_FAILED,
            status=AgentEventStatus.FAILED,
            role=role,
            phase=phase,
            failure_class=failure_class,
            detail=detail,
        )

    @classmethod
    def phase_started(cls, phase: str) -> AgentEvent:
        return cls(
            event=AgentEventName.PHASE_STARTED,
            status=AgentEventStatus.RUNNING,
            phase=phase,
        )

    @classmethod
    def gate_passed(cls, phase: str, detail: str | None = None) -> AgentEvent:
        return cls(
            event=AgentEventName.PHASE_GATE_PASSED,
            status=AgentEventStatus.GREEN,
            phase=phase,
            detail=detail,
        )

    @classmethod
    def gate_failed(cls, phase: str, detail: str | None = None) -> AgentEvent:
        return cls(
            event=AgentEventName.PHASE_GATE_FAILED,
            status=AgentEventStatus.RED,
            phase=phase,
            detail=detail,
        )

    @classmethod
    def recovery_attempted(
        cls, role: str, phase: str, detail: str | None = None
    ) -> AgentEvent:
        return cls(
            event=AgentEventName.RECOVERY_ATTEMPTED,
            status=AgentEventStatus.RUNNING,
            role=role,
            phase=phase,
            detail=detail,
        )

    # -- Serialization --

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict. Includes 'type' for backward compat."""
        d: dict[str, Any] = {
            "event": self.event.value,
            "type": self.event.value.split(".")[-1],  # backward compat
            "status": self.status.value,
            "emitted_at": self.emitted_at,
        }
        if self.role is not None:
            d["role"] = self.role
        if self.phase is not None:
            d["phase"] = self.phase
        if self.project_id is not None:
            d["project_id"] = self.project_id
        if self.failure_class is not None:
            d["failure_class"] = self.failure_class.value
        if self.detail is not None:
            d["detail"] = self.detail
        if self.data is not None:
            d["data"] = self.data
        return d

    def to_json(self) -> str:
        """JSON string representation."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentEvent:
        """Reconstruct AgentEvent from dict. Handles enum conversions."""
        event_raw = data.get("event")
        if isinstance(event_raw, AgentEventName):
            event = event_raw
        elif isinstance(event_raw, str):
            event = AgentEventName(event_raw)
        else:
            raise ValueError(f"event field missing or invalid: {event_raw!r}")

        status_raw = data.get("status")
        if isinstance(status_raw, AgentEventStatus):
            status = status_raw
        elif isinstance(status_raw, str):
            status = AgentEventStatus(status_raw)
        else:
            raise ValueError(f"status field missing or invalid: {status_raw!r}")

        failure_class: AgentFailureClass | None = None
        fc_raw = data.get("failure_class")
        if isinstance(fc_raw, AgentFailureClass):
            failure_class = fc_raw
        elif isinstance(fc_raw, str):
            failure_class = AgentFailureClass(fc_raw)

        return cls(
            event=event,
            status=status,
            emitted_at=data.get("emitted_at", _now_iso()),
            role=data.get("role"),
            phase=data.get("phase"),
            project_id=data.get("project_id"),
            failure_class=failure_class,
            detail=data.get("detail"),
            data=data.get("data"),
        )

    @classmethod
    def from_json(cls, s: str) -> AgentEvent:
        """Reconstruct AgentEvent from JSON string."""
        data = json.loads(s)
        return cls.from_dict(data)

    def to_otel_attributes(self) -> dict[str, str | int | float]:
        """Convert to OpenTelemetry semantic convention attributes."""
        attrs: dict[str, str | int | float] = {
            "agent.event": self.event.value,
            "agent.event.status": self.status.value,
            "agent.event.timestamp": self.emitted_at,
        }
        if self.role is not None:
            attrs["agent.role"] = self.role
        if self.phase is not None:
            attrs["agent.phase"] = self.phase
        if self.project_id is not None:
            attrs["agent.project_id"] = self.project_id
        if self.failure_class is not None:
            attrs["agent.failure_class"] = self.failure_class.value
        if self.detail is not None:
            attrs["agent.detail"] = self.detail
        return attrs
