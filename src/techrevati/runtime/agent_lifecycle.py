"""
Agent Lifecycle — Agent state machine with event log.

Explicit agent lifecycle states with validated transitions. Every
state change is recorded as an event with timestamp and detail.
AgentRegistry provides thread-safe concurrent access.
"""

from __future__ import annotations

import logging
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger("techrevati.runtime.lifecycle")
logger.addHandler(logging.NullHandler())


class AgentStatus(str, Enum):
    """Agent lifecycle states with valid transition paths."""

    IDLE = "idle"
    INITIALIZING = "initializing"
    WAITING_FOR_INPUT = "waiting_for_input"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid state transitions. CANCELLED is reachable from any non-terminal state
# (caller-driven cancellation, asyncio.CancelledError, timeout, etc.).
_VALID_TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    AgentStatus.IDLE: {
        AgentStatus.INITIALIZING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.INITIALIZING: {
        AgentStatus.WAITING_FOR_INPUT,
        AgentStatus.RUNNING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.WAITING_FOR_INPUT: {
        AgentStatus.RUNNING,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.RUNNING: {
        AgentStatus.WAITING_FOR_INPUT,
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    },
    AgentStatus.COMPLETED: set(),  # terminal
    AgentStatus.FAILED: set(),  # terminal
    AgentStatus.CANCELLED: set(),  # terminal
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: AgentStatus, target: AgentStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current.value} → {target.value}")


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


def _validate_project_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("project_id must be an integer or None")
    if value < 0:
        raise ValueError("project_id must be non-negative")
    return value


def _validate_required_project_id(value: int) -> int:
    project_id = _validate_project_id(value)
    assert project_id is not None
    return project_id


def _validate_non_negative_int(field_name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _validate_positive_int(field_name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _coerce_status(field_name: str, value: AgentStatus | str) -> AgentStatus:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be an AgentStatus")
    if isinstance(value, AgentStatus):
        return value
    if isinstance(value, str):
        try:
            return AgentStatus(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid AgentStatus") from exc
    raise TypeError(f"{field_name} must be an AgentStatus")


@dataclass(frozen=True)
class AgentWorkerEvent:
    """Immutable record of a state transition."""

    seq: int
    kind: str
    status: str
    detail: str | None
    timestamp: str

    def __post_init__(self) -> None:
        seq = _validate_positive_int("seq", self.seq)
        status = _coerce_status("status", self.status)
        kind = _validate_non_empty_str("kind", self.kind)
        detail = _validate_optional_str("detail", self.detail)
        timestamp = _validate_non_empty_str("timestamp", self.timestamp)
        object.__setattr__(self, "seq", seq)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status.value)
        object.__setattr__(self, "detail", detail)
        object.__setattr__(self, "timestamp", timestamp)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "seq": self.seq,
            "kind": self.kind,
            "status": self.status,
            "timestamp": self.timestamp,
        }
        if self.detail is not None:
            d["detail"] = self.detail
        return d


@dataclass
class AgentWorker:
    """Tracks an agent's lifecycle through execution."""

    worker_id: str
    role: str
    phase: str
    project_id: int | None = None
    status: AgentStatus = AgentStatus.IDLE
    events: list[AgentWorkerEvent] = field(default_factory=list)
    retry_count: int = 0
    last_error: dict[str, Any] | None = None
    provider_used: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    _lock: Any = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.worker_id = _validate_non_empty_str("worker_id", self.worker_id)
        self.role = _validate_non_empty_str("role", self.role)
        self.phase = _validate_non_empty_str("phase", self.phase)
        self.project_id = _validate_project_id(self.project_id)
        self.status = _coerce_status("status", self.status)
        if not isinstance(self.events, list):
            raise TypeError("events must be a list")
        events: list[AgentWorkerEvent] = []
        for event in self.events:
            if not isinstance(event, AgentWorkerEvent):
                raise TypeError("events must contain AgentWorkerEvent instances")
            events.append(event)
        self.events = events
        self.retry_count = _validate_non_negative_int("retry_count", self.retry_count)
        if self.last_error is not None and not isinstance(self.last_error, dict):
            raise TypeError("last_error must be a dict or None")
        if self.last_error is not None:
            self.last_error = deepcopy(self.last_error)
        self.provider_used = _validate_optional_str("provider_used", self.provider_used)
        self.created_at = _validate_non_empty_str("created_at", self.created_at)
        self.updated_at = _validate_non_empty_str("updated_at", self.updated_at)

    def transition(
        self, new_status: AgentStatus | str, detail: str | None = None
    ) -> AgentWorkerEvent:
        """Validate and execute a state transition. Returns the new event."""
        target_status = _coerce_status("new_status", new_status)
        detail = _validate_optional_str("detail", detail)
        with self._lock:
            valid = _VALID_TRANSITIONS.get(self.status, set())
            if target_status not in valid:
                raise InvalidTransitionError(self.status, target_status)

            event = AgentWorkerEvent(
                seq=len(self.events) + 1,
                kind=target_status.value,
                status=target_status.value,
                detail=detail,
                timestamp=_now_iso(),
            )
            self.events.append(event)
            self.status = target_status
            self.updated_at = event.timestamp

            if target_status == AgentStatus.FAILED and detail:
                self.last_error = {"message": detail, "timestamp": event.timestamp}

            return event

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self.status in (
                AgentStatus.COMPLETED,
                AgentStatus.FAILED,
                AgentStatus.CANCELLED,
            )

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "worker_id": self.worker_id,
                "role": self.role,
                "phase": self.phase,
                "project_id": self.project_id,
                "status": self.status.value,
                "retry_count": self.retry_count,
                "last_error": deepcopy(self.last_error),
                "provider_used": self.provider_used,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "events": [e.to_dict() for e in self.events],
            }


class AgentRegistry:
    """Thread-safe registry of active AgentWorker instances."""

    def __init__(self) -> None:
        self._workers: dict[str, AgentWorker] = {}
        self._lock = threading.Lock()

    def create(
        self,
        role: str,
        phase: str,
        project_id: int | None = None,
    ) -> AgentWorker:
        role = _validate_non_empty_str("role", role)
        phase = _validate_non_empty_str("phase", phase)
        project_id = _validate_project_id(project_id)
        worker_id = f"{role}-{phase}-{uuid.uuid4().hex[:8]}"
        worker = AgentWorker(
            worker_id=worker_id,
            role=role,
            phase=phase,
            project_id=project_id,
        )
        with self._lock:
            self._workers[worker_id] = worker
        return worker

    def get(self, worker_id: str) -> AgentWorker | None:
        worker_id = _validate_non_empty_str("worker_id", worker_id)
        with self._lock:
            return self._workers.get(worker_id)

    def transition(
        self,
        worker_id: str,
        status: AgentStatus | str,
        detail: str | None = None,
    ) -> AgentWorker:
        worker_id = _validate_non_empty_str("worker_id", worker_id)
        status = _coerce_status("status", status)
        detail = _validate_optional_str("detail", detail)
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                raise KeyError(f"Worker not found: {worker_id}")
            worker.transition(status, detail)
            return worker

    def list_active(self) -> list[AgentWorker]:
        with self._lock:
            return [w for w in self._workers.values() if not w.is_terminal]

    def get_by_role_phase(self, role: str, phase: str) -> AgentWorker | None:
        role = _validate_non_empty_str("role", role)
        phase = _validate_non_empty_str("phase", phase)
        with self._lock:
            for w in self._workers.values():
                if w.role == role and w.phase == phase and not w.is_terminal:
                    return w
            return None

    def get_by_project(self, project_id: int) -> list[AgentWorker]:
        project_id = _validate_required_project_id(project_id)
        with self._lock:
            return [w for w in self._workers.values() if w.project_id == project_id]

    def clear(self) -> None:
        """Clear all workers (for testing)."""
        with self._lock:
            self._workers.clear()
