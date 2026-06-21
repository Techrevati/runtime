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

The pattern records delegation between sessions without running the target
agent directly.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from techrevati.runtime._internal import (
    _validate_non_empty_str,
    _validate_project_id,
)


def _validate_optional_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if value and not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _normalize_context(context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise TypeError("context must be a dict")
    copied = deepcopy(context)
    for key in copied:
        if not isinstance(key, str):
            raise TypeError("context keys must be strings")
    return copied


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

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_role",
            _validate_non_empty_str("source_role", self.source_role),
        )
        object.__setattr__(
            self,
            "target_role",
            _validate_non_empty_str("target_role", self.target_role),
        )
        object.__setattr__(self, "phase", _validate_non_empty_str("phase", self.phase))
        object.__setattr__(
            self, "reason", _validate_non_empty_str("reason", self.reason)
        )
        object.__setattr__(self, "context", _normalize_context(self.context))
        object.__setattr__(self, "project_id", _validate_project_id(self.project_id))
        object.__setattr__(
            self,
            "target_worker_id",
            _validate_optional_str("target_worker_id", self.target_worker_id),
        )
        object.__setattr__(
            self, "created_at", _validate_non_empty_str("created_at", self.created_at)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_role": self.source_role,
            "target_role": self.target_role,
            "phase": self.phase,
            "reason": self.reason,
            "context": deepcopy(self.context),
            "project_id": self.project_id,
            "target_worker_id": self.target_worker_id,
            "created_at": self.created_at,
        }


__all__ = ["Handoff"]
