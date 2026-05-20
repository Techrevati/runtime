"""
Handoffs — delegation between agents.

A ``Handoff`` records the intent of one agent (the *source*) to pass
control to another (the *target*), with a structured reason and
caller-provided context payload. The source session finalizes its
worker as ``COMPLETED``; a new worker is created in the registry under
the target role so downstream code can pick it up by ``worker_id``.

The caller resolves the handoff by opening a new session for the
target role. This module does not run the target agent — it just
records and routes.

The pattern mirrors OpenAI Agents SDK handoffs and the
orchestrator-workers delegation workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Handoff:
    """Immutable record of an agent-to-agent delegation.

    Created by ``OrchestrationSession.handoff_to`` /
    ``AsyncOrchestrationSession.handoff_to``. Use ``target_worker_id`` to
    look up the freshly-registered worker from the same ``AgentRegistry``
    the source session was using.
    """

    source_role: str
    target_role: str
    phase: str
    reason: str
    context: dict[str, Any] = field(default_factory=dict)
    project_id: int | None = None
    target_worker_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_role": self.source_role,
            "target_role": self.target_role,
            "phase": self.phase,
            "reason": self.reason,
            "context": dict(self.context),
            "project_id": self.project_id,
            "target_worker_id": self.target_worker_id,
            "created_at": self.created_at,
        }


__all__ = ["Handoff"]
