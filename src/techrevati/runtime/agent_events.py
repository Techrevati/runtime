"""
Agent Events — Typed lifecycle events with a failure taxonomy.

Provides a structured event schema for agent execution. Events are
JSON-serializable and include both an 'event' (full path) and 'type'
(short tail) field so they can be routed by either consumers.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
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
    # Governance plane (Sprint 2 / EU AI Act Article 14 + 15)
    GOVERNANCE_BREACH = "governance.breach"
    GOVERNANCE_ALERT = "governance.alert"


class AgentEventStatus(str, Enum):
    """Current status at time of event emission."""

    RUNNING = "running"
    READY = "ready"
    BLOCKED = "blocked"
    GREEN = "green"
    RED = "red"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentFailureClass(str, Enum):
    """Failure taxonomy for structured error classification."""

    LLM_TIMEOUT = "llm_timeout"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    DEPENDENCY_FAILED = "dependency_failed"
    GOVERNANCE_BREACH = "governance_breach"
    PERMISSION_DENIED = "permission_denied"
    GUARDRAIL_VIOLATION = "guardrail_violation"
    MEMORY_CORRUPTION = "memory_corruption"
    VALIDATION_ERROR = "validation_error"
    PROMPT_REJECTION = "prompt_rejection"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


def _status_for_failed_event(failure_class: AgentFailureClass) -> AgentEventStatus:
    if failure_class == AgentFailureClass.CANCELLED:
        return AgentEventStatus.CANCELLED
    return AgentEventStatus.FAILED


def _validate_failed_event_status(
    event: AgentEventName,
    status: AgentEventStatus,
    failure_class: AgentFailureClass | None,
) -> None:
    if event != AgentEventName.AGENT_FAILED:
        return
    if failure_class is None:
        raise ValueError("agent.failed events require failure_class")
    if failure_class == AgentFailureClass.CANCELLED:
        if status != AgentEventStatus.CANCELLED:
            raise ValueError(
                "agent.failed cancellation events must use status cancelled"
            )
        return
    if status == AgentEventStatus.CANCELLED:
        raise ValueError(
            "agent.failed status cancelled requires failure_class cancelled"
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_non_empty_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_optional_str(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    return value


def _validate_optional_non_empty_str(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _validate_non_empty_str(field_name, value)


def _coerce_event_name(value: AgentEventName | str) -> AgentEventName:
    if isinstance(value, AgentEventName):
        return value
    if isinstance(value, str):
        try:
            return AgentEventName(value)
        except ValueError as exc:
            raise ValueError("event must be a valid AgentEventName") from exc
    raise TypeError("event must be an AgentEventName")


def _coerce_status(value: AgentEventStatus | str) -> AgentEventStatus:
    if isinstance(value, AgentEventStatus):
        return value
    if isinstance(value, str):
        try:
            return AgentEventStatus(value)
        except ValueError as exc:
            raise ValueError("status must be a valid AgentEventStatus") from exc
    raise TypeError("status must be an AgentEventStatus")


def _coerce_failure_class(
    value: AgentFailureClass | str | None,
) -> AgentFailureClass | None:
    if value is None:
        return None
    if isinstance(value, AgentFailureClass):
        return value
    if isinstance(value, str):
        try:
            return AgentFailureClass(value)
        except ValueError as exc:
            raise ValueError("failure_class must be a valid AgentFailureClass") from exc
    raise TypeError("failure_class must be an AgentFailureClass or None")


def _validate_project_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("project_id must be an integer or None")
    if value < 0:
        raise ValueError("project_id must be non-negative")
    return value


def _copy_data(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise TypeError("data must be a dict or None")
    copied = deepcopy(data)
    for key in copied:
        if not isinstance(key, str):
            raise TypeError("data keys must be strings")
    return copied


def _validate_finite_number(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "event", _coerce_event_name(self.event))
        object.__setattr__(self, "status", _coerce_status(self.status))
        object.__setattr__(
            self, "emitted_at", _validate_non_empty_str("emitted_at", self.emitted_at)
        )
        object.__setattr__(
            self, "role", _validate_optional_non_empty_str("role", self.role)
        )
        object.__setattr__(
            self, "phase", _validate_optional_non_empty_str("phase", self.phase)
        )
        object.__setattr__(self, "project_id", _validate_project_id(self.project_id))
        object.__setattr__(
            self, "failure_class", _coerce_failure_class(self.failure_class)
        )
        _validate_failed_event_status(self.event, self.status, self.failure_class)
        object.__setattr__(
            self, "detail", _validate_optional_str("detail", self.detail)
        )
        object.__setattr__(self, "data", _copy_data(self.data))

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
    def ready(
        cls,
        role: str,
        phase: str,
        detail: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        return cls(
            event=AgentEventName.AGENT_READY,
            status=AgentEventStatus.READY,
            role=role,
            phase=phase,
            detail=detail,
            data=data,
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
    def blocked(
        cls,
        role: str,
        phase: str,
        detail: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        return cls(
            event=AgentEventName.AGENT_BLOCKED,
            status=AgentEventStatus.BLOCKED,
            role=role,
            phase=phase,
            detail=detail,
            data=data,
        )

    @classmethod
    def tool_called(cls, role: str, phase: str, tool: str) -> AgentEvent:
        return cls(
            event=AgentEventName.AGENT_TOOL_CALLED,
            status=AgentEventStatus.RUNNING,
            role=role,
            phase=phase,
            data={"tool": tool},
        )

    @classmethod
    def tool_completed(cls, role: str, phase: str, tool: str) -> AgentEvent:
        return cls(
            event=AgentEventName.AGENT_TOOL_COMPLETED,
            status=AgentEventStatus.COMPLETED,
            role=role,
            phase=phase,
            data={"tool": tool},
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
            status=_status_for_failed_event(failure_class),
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

    @classmethod
    def recovery_succeeded(
        cls,
        role: str,
        phase: str,
        detail: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        return cls(
            event=AgentEventName.RECOVERY_SUCCEEDED,
            status=AgentEventStatus.RUNNING,
            role=role,
            phase=phase,
            detail=detail,
            data=data,
        )

    @classmethod
    def recovery_failed(
        cls,
        role: str,
        phase: str,
        detail: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        return cls(
            event=AgentEventName.RECOVERY_FAILED,
            status=AgentEventStatus.FAILED,
            role=role,
            phase=phase,
            detail=detail,
            data=data,
        )

    @classmethod
    def recovery_escalated(
        cls,
        role: str,
        phase: str,
        detail: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> AgentEvent:
        return cls(
            event=AgentEventName.RECOVERY_ESCALATED,
            status=AgentEventStatus.FAILED,
            role=role,
            phase=phase,
            detail=detail,
            data=data,
        )

    @classmethod
    def governance_breach(
        cls,
        role: str,
        phase: str,
        *,
        limit_name: str,
        observed: float,
        ceiling: float,
        scope: str,
    ) -> AgentEvent:
        observed = _validate_finite_number("observed", observed)
        ceiling = _validate_finite_number("ceiling", ceiling)
        limit_name = _validate_non_empty_str("limit_name", limit_name)
        scope = _validate_non_empty_str("scope", scope)
        return cls(
            event=AgentEventName.GOVERNANCE_BREACH,
            status=AgentEventStatus.FAILED,
            role=role,
            phase=phase,
            failure_class=AgentFailureClass.GOVERNANCE_BREACH,
            detail=f"{limit_name}: {observed} > {ceiling}",
            data={
                "limit_name": limit_name,
                "observed": observed,
                "ceiling": ceiling,
                "scope": scope,
            },
        )

    @classmethod
    def governance_alert(
        cls,
        role: str,
        phase: str,
        *,
        limit_name: str,
        observed: float,
        ceiling: float,
        scope: str,
    ) -> AgentEvent:
        observed = _validate_finite_number("observed", observed)
        ceiling = _validate_finite_number("ceiling", ceiling)
        limit_name = _validate_non_empty_str("limit_name", limit_name)
        scope = _validate_non_empty_str("scope", scope)
        return cls(
            event=AgentEventName.GOVERNANCE_ALERT,
            status=AgentEventStatus.RUNNING,
            role=role,
            phase=phase,
            detail=f"{limit_name}: {observed} > {ceiling}",
            data={
                "limit_name": limit_name,
                "observed": observed,
                "ceiling": ceiling,
                "scope": scope,
            },
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
            d["data"] = deepcopy(self.data)
        return d

    def to_json(self) -> str:
        """JSON string representation."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentEvent:
        """Reconstruct AgentEvent from dict. Handles enum conversions."""
        if not isinstance(data, dict):
            raise TypeError("data must be a dict")
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
        if not isinstance(data, dict):
            raise TypeError("JSON payload must be an object")
        return cls.from_dict(data)

    def to_otel_attributes(self) -> dict[str, str | int | float]:
        """Convert to semantic-convention-style telemetry attributes."""
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
