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
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


# Valid state transitions
_VALID_TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    AgentStatus.IDLE: {AgentStatus.INITIALIZING, AgentStatus.FAILED},
    AgentStatus.INITIALIZING: {AgentStatus.WAITING_FOR_INPUT, AgentStatus.RUNNING, AgentStatus.FAILED},
    AgentStatus.WAITING_FOR_INPUT: {AgentStatus.RUNNING, AgentStatus.FAILED},
    AgentStatus.RUNNING: {AgentStatus.COMPLETED, AgentStatus.FAILED},
    AgentStatus.COMPLETED: set(),  # terminal
    AgentStatus.FAILED: set(),     # terminal
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: AgentStatus, target: AgentStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transition: {current.value} → {target.value}"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AgentWorkerEvent:
    """Immutable record of a state transition."""
    seq: int
    kind: str
    status: str
    detail: str | None
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "seq": self.seq,
            "kind": self.kind,
            "status": self.status,
            "timestamp": self.timestamp,
        }
        if self.detail:
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

    def transition(
        self, new_status: AgentStatus, detail: str | None = None
    ) -> AgentWorkerEvent:
        """Validate and execute a state transition. Returns the new event."""
        valid = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid:
            raise InvalidTransitionError(self.status, new_status)

        event = AgentWorkerEvent(
            seq=len(self.events) + 1,
            kind=new_status.value,
            status=new_status.value,
            detail=detail,
            timestamp=_now_iso(),
        )
        self.events.append(event)
        self.status = new_status
        self.updated_at = event.timestamp

        if new_status == AgentStatus.FAILED and detail:
            self.last_error = {"message": detail, "timestamp": event.timestamp}

        return event

    @property
    def is_terminal(self) -> bool:
        return self.status in (AgentStatus.COMPLETED, AgentStatus.FAILED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "role": self.role,
            "phase": self.phase,
            "project_id": self.project_id,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
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
        with self._lock:
            return self._workers.get(worker_id)

    def transition(
        self,
        worker_id: str,
        status: AgentStatus,
        detail: str | None = None,
    ) -> AgentWorker:
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
        with self._lock:
            for w in self._workers.values():
                if w.role == role and w.phase == phase and not w.is_terminal:
                    return w
            return None

    def get_by_project(self, project_id: int) -> list[AgentWorker]:
        with self._lock:
            return [w for w in self._workers.values() if w.project_id == project_id]

    def clear(self) -> None:
        """Clear all workers (for testing)."""
        with self._lock:
            self._workers.clear()
