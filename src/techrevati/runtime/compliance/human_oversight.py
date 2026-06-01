"""
Human oversight — pause / review / override primitives (EU AI Act Article 14).

Article 14 requires that a high-risk AI system can be effectively overseen by a
natural person who can interpret outputs, intervene, and decide whether to use,
override, or stop the system.

This module provides:

- :class:`ReviewerIdentity` / :class:`ReviewDecision` — who decided, and what.
- :class:`ReviewQueue` — a caller-implemented async bridge to whatever UI / Slack
  / ticketing system actually surfaces the decision to a human.
- :class:`HumanOversightInterface` — pauses a turn for review (the worker sits in
  ``AgentStatus.WAITING_FOR_INPUT`` while awaiting the queue), records the request
  and resolution to the tamper-evident audit log, and exposes a manual
  :meth:`override` (Article 14(4)(d)).
- :class:`ExplanationReport` — a reviewer-readable summary of one turn
  (Article 14(4)(c)).

.. warning::

    Engineering primitive, not legal advice. *Effective* oversight depends on
    reviewer competence and process, which this library cannot provide.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from techrevati.runtime.agent_events import AgentEvent
from techrevati.runtime.compliance.audit_log import AuditLogSink

__all__ = [
    "ExplanationReport",
    "HumanOversightInterface",
    "ReviewDecision",
    "ReviewQueue",
    "ReviewTimeoutError",
    "ReviewerIdentity",
    "StaticReviewQueue",
]

ReviewOutcome = Literal["approve", "reject", "modify", "escalate"]
OnTimeout = Literal["abort", "proceed_with_warning"]


def _now() -> datetime:
    return datetime.now(UTC)


class ReviewTimeoutError(Exception):
    """Raised when a review times out and ``on_timeout="abort"``."""

    def __init__(self, decision_id: str) -> None:
        self.decision_id = decision_id
        super().__init__(f"human review timed out: {decision_id}")


@dataclass(frozen=True)
class ReviewerIdentity:
    """Identifies the natural person accountable for a decision (Article 12(3))."""

    id: str
    role: str
    authentication_method: str = "unspecified"
    authenticated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("ReviewerIdentity.id must be non-empty")
        if not self.role.strip():
            raise ValueError("ReviewerIdentity.role must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "authentication_method": self.authentication_method,
            "authenticated_at": self.authenticated_at.isoformat(),
        }


#: Synthetic reviewer for system-decided outcomes (timeouts, automated overrides).
SYSTEM_REVIEWER = ReviewerIdentity(id="system", role="runtime")


@dataclass(frozen=True)
class ReviewDecision:
    """A human (or system-on-timeout) decision about a paused turn."""

    decision: ReviewOutcome
    reason: str
    reviewer: ReviewerIdentity
    modifications: dict[str, Any] | None = None
    decided_at: datetime = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "decision": self.decision,
            "reason": self.reason,
            "reviewer": self.reviewer.to_dict(),
            "decided_at": self.decided_at.isoformat(),
        }
        if self.modifications is not None:
            d["modifications"] = self.modifications
        return d


@runtime_checkable
class ReviewQueue(Protocol):
    """Caller-implemented bridge to the human review channel.

    ``enqueue`` blocks (asynchronously) until a human responds or the timeout
    elapses; return ``None`` to signal a timeout.
    """

    async def enqueue(
        self,
        decision_id: str,
        context: dict[str, Any],
        timeout_seconds: float,
    ) -> ReviewDecision | None: ...


@dataclass
class StaticReviewQueue:
    """In-memory queue that returns pre-seeded decisions; else times out.

    Useful for tests and deterministic demos. Seed with
    ``StaticReviewQueue({decision_id: ReviewDecision(...)})``.
    """

    decisions: dict[str, ReviewDecision] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list, init=False)

    async def enqueue(
        self,
        decision_id: str,
        context: dict[str, Any],
        timeout_seconds: float,
    ) -> ReviewDecision | None:
        self.requested.append(decision_id)
        return self.decisions.get(decision_id)


class HumanOversightInterface:
    """Article 14 human-oversight primitive: pause for review + manual override."""

    def __init__(
        self,
        queue: ReviewQueue,
        *,
        require_review_for: Iterable[str] = (),
        default_timeout_seconds: float = 1800.0,
        on_timeout: OnTimeout = "abort",
        audit_log: AuditLogSink | None = None,
        role: str = "agent",
        phase: str = "oversight",
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        if on_timeout not in ("abort", "proceed_with_warning"):
            raise ValueError("on_timeout must be 'abort' or 'proceed_with_warning'")
        self._queue = queue
        self._require_review_for = frozenset(require_review_for)
        self._default_timeout = float(default_timeout_seconds)
        self._on_timeout = on_timeout
        self._audit = audit_log
        self._role = role
        self._phase = phase

    def requires_review(self, event_type: str) -> bool:
        """Whether ``event_type`` is configured to demand a human decision."""
        return event_type in self._require_review_for

    async def pause_for_review(
        self,
        decision_id: str,
        context: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> ReviewDecision:
        """Pause the turn until a human resolves it (or the timeout decides).

        Records ``oversight.review_requested`` then ``oversight.review_resolved``
        (with the reviewer id) to the audit log, so the Article 12 trail shows
        who decided and when.
        """
        if not decision_id.strip():
            raise ValueError("decision_id must be non-empty")
        timeout = self._default_timeout if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._emit(
            AgentEvent.oversight_review_requested(
                self._role, self._phase, decision_id=decision_id
            )
        )
        decision = await self._queue.enqueue(decision_id, context, timeout)
        timed_out = decision is None
        if decision is None:
            decision = self._on_timeout_decision(decision_id)

        self._emit(
            AgentEvent.oversight_review_resolved(
                self._role,
                self._phase,
                decision_id=decision_id,
                decision=decision.decision,
                reviewer_id=decision.reviewer.id,
            )
        )
        if timed_out and self._on_timeout == "abort":
            raise ReviewTimeoutError(decision_id)
        return decision

    def override(
        self,
        reason: str,
        reviewer: ReviewerIdentity,
        *,
        action: Literal["stop", "modify_next_turn", "skip_governance"],
        decision_id: str = "manual-override",
    ) -> ReviewDecision:
        """Record a manual human override (Article 14(4)(d))."""
        if not reason.strip():
            raise ValueError("override reason must be non-empty")
        outcome: ReviewOutcome = "reject" if action == "stop" else "modify"
        decision = ReviewDecision(
            decision=outcome,
            reason=reason,
            reviewer=reviewer,
            modifications={"action": action},
        )
        self._emit(
            AgentEvent.oversight_review_resolved(
                self._role,
                self._phase,
                decision_id=decision_id,
                decision=decision.decision,
                reviewer_id=reviewer.id,
            )
        )
        return decision

    def _on_timeout_decision(self, decision_id: str) -> ReviewDecision:
        if self._on_timeout == "abort":
            return ReviewDecision(
                decision="reject",
                reason="review timed out (abort)",
                reviewer=SYSTEM_REVIEWER,
            )
        return ReviewDecision(
            decision="approve",
            reason="review timed out (proceeded with warning)",
            reviewer=SYSTEM_REVIEWER,
        )

    def _emit(self, event: AgentEvent) -> None:
        if self._audit is not None:
            self._audit.emit(event)


@dataclass(frozen=True)
class ExplanationReport:
    """A reviewer-readable summary of one turn (Article 14(4)(c))."""

    turn_id: str
    role: str | None
    phase: str | None
    event_count: int
    tools_invoked: tuple[str, ...]
    failures: tuple[str, ...]
    final_status: str | None

    @classmethod
    def from_events(
        cls, turn_id: str, events: Iterable[AgentEvent]
    ) -> ExplanationReport:
        events = list(events)
        tools: list[str] = []
        failures: list[str] = []
        role = phase = None
        final_status = None
        for event in events:
            role = event.role or role
            phase = event.phase or phase
            final_status = event.status.value
            if event.data and "tool" in event.data:
                tool = str(event.data["tool"])
                if tool not in tools:
                    tools.append(tool)
            if event.failure_class is not None:
                failures.append(event.failure_class.value)
        return cls(
            turn_id=turn_id,
            role=role,
            phase=phase,
            event_count=len(events),
            tools_invoked=tuple(tools),
            failures=tuple(failures),
            final_status=final_status,
        )

    def to_markdown(self) -> str:
        lines = [
            f"# Turn explanation — {self.turn_id}",
            "",
            f"- Role: {self.role or '—'}",
            f"- Phase: {self.phase or '—'}",
            f"- Events: {self.event_count}",
            f"- Final status: {self.final_status or '—'}",
            f"- Tools invoked: {', '.join(self.tools_invoked) or '—'}",
            f"- Failures: {', '.join(self.failures) or 'none'}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "role": self.role,
            "phase": self.phase,
            "event_count": self.event_count,
            "tools_invoked": list(self.tools_invoked),
            "failures": list(self.failures),
            "final_status": self.final_status,
        }
