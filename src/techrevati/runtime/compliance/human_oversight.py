"""
Human oversight — pause / review / override primitives (EU AI Act Article 14).

Article 14 requires that a high-risk AI system can be effectively overseen by a
natural person who can interpret outputs, intervene, and decide whether to use,
override, or stop the system.

This module provides:

- :class:`ReviewerIdentity` / :class:`ReviewDecision` — who decided, and what.
- :class:`ReviewQueue` — a caller-implemented async bridge to whatever UI / Slack
  / ticketing system actually surfaces the decision to a human.
- :class:`HumanOversightInterface` — a standalone async decision gate. The caller
  awaits :meth:`~HumanOversightInterface.pause_for_review` at a high-stakes point;
  it records the request and resolution to the tamper-evident audit log and returns
  the human's :class:`ReviewDecision` (or the configured on-timeout decision). It
  does **not** transition worker lifecycle status or auto-pause ``AgentSession``
  turns — reflecting ``AgentStatus.WAITING_FOR_INPUT`` around the ``await`` is the
  caller's responsibility. Also exposes a manual :meth:`override` (Article 14(4)(d)).
- :class:`ExplanationReport` — a reviewer-readable summary of one turn
  (Article 14(4)(c)).

.. warning::

    Engineering primitive, not legal advice. *Effective* oversight depends on
    reviewer competence and process, which this library cannot provide.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, get_args, runtime_checkable

from techrevati.runtime.agent_events import AgentEvent
from techrevati.runtime.compliance.audit_log import (
    AuditLogSink,
    _canonical_json,
    _hash_bytes,
    _normalize_payload,
)
from techrevati.runtime.permissions import _match_key

__all__ = [
    "ExplanationReport",
    "HumanOversightInterface",
    "InvalidReviewDecisionError",
    "ReviewDecision",
    "ReviewQueue",
    "ReviewTimeoutError",
    "ReviewerIdentity",
    "StaticReviewQueue",
]

ReviewOutcome = Literal["approve", "reject", "modify", "escalate"]
OnTimeout = Literal["abort", "proceed_with_warning"]
OverrideAction = Literal["stop", "modify_next_turn", "skip_governance"]

_VALID_OUTCOMES = frozenset(get_args(ReviewOutcome))
_VALID_OVERRIDE_ACTIONS = frozenset(get_args(OverrideAction))
#: Tolerated clock difference between this host and the review channel.
_DECISION_CLOCK_SKEW = timedelta(minutes=5)
#: Resolved decision ids remembered for replay rejection (oldest evicted first).
_MAX_TRACKED_DECISIONS = 65_536

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_timeout(name: str, value: float) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return timeout


def _context_digest(context: dict[str, Any]) -> str:
    """Fingerprint of what was put in front of the reviewer, for the audit trail."""
    text = _canonical_json(_normalize_payload(context))
    return hashlib.sha256(_hash_bytes(text)).hexdigest()


class ReviewTimeoutError(Exception):
    """Raised when a review times out and ``on_timeout="abort"``."""

    def __init__(self, decision_id: str) -> None:
        self.decision_id = decision_id
        super().__init__(f"human review timed out: {decision_id}")


class InvalidReviewDecisionError(ValueError):
    """A review gate refused a decision: replayed id, stale, or not from a human."""


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

    def __post_init__(self) -> None:
        if self.decision not in _VALID_OUTCOMES:
            raise ValueError(
                f"decision must be one of {sorted(_VALID_OUTCOMES)}: {self.decision!r}"
            )

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
    elapses; return ``None`` to signal a timeout. The gate also enforces the
    timeout itself, so a hung channel cannot hold a decision open indefinitely.
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
    """Article 14 human-oversight primitive: a standalone async pause-for-review
    gate plus a manual override. It records to the audit log and returns the
    decision; it does not itself change worker status or auto-pause sessions.

    Each ``decision_id`` resolves at most once per interface instance. The record
    of resolved ids is in-memory and bounded, so make ids unique per decision and
    do not rely on this check across restarts."""

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
        default_timeout_seconds = _validate_timeout(
            "default_timeout_seconds", default_timeout_seconds
        )
        if on_timeout not in ("abort", "proceed_with_warning"):
            raise ValueError("on_timeout must be 'abort' or 'proceed_with_warning'")
        self._queue = queue
        self._require_review_for = frozenset(require_review_for)
        self._default_timeout = default_timeout_seconds
        self._on_timeout = on_timeout
        self._audit = audit_log
        self._role = role
        self._phase = phase
        self._claimed: OrderedDict[str, None] = OrderedDict()

    @property
    def audit_log(self) -> AuditLogSink | None:
        """The audit log this interface records to, if any."""
        return self._audit

    def attach_audit_log(self, audit_log: AuditLogSink) -> None:
        """Record to ``audit_log`` unless a log was already configured explicitly."""
        if self._audit is None:
            self._audit = audit_log

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
        """Await a human decision for ``decision_id`` (or let the timeout decide).

        This blocks the *calling coroutine*, not any worker — the caller awaits it
        at a decision point; it does not transition worker lifecycle status. Records
        ``oversight.review_requested`` then ``oversight.review_resolved`` to the
        audit log, with the reviewer id, reason, and a fingerprint of ``context``,
        so the Article 12 trail shows who decided what, when, and why.

        The gate fails closed: a replayed ``decision_id``, a decision dated outside
        the review window, or one claiming the system reviewer raises
        :class:`InvalidReviewDecisionError`. A queue failure is recorded and
        re-raised, and the id can then be retried; so is a decision whose audit
        record could not be written, since an unrecorded decision did not take effect.
        """
        if not decision_id.strip():
            raise ValueError("decision_id must be non-empty")
        if decision_id != decision_id.strip():
            # " d1" would otherwise claim a new id and get around replay rejection.
            raise ValueError("decision_id must not have surrounding whitespace")
        timeout = (
            self._default_timeout
            if timeout_seconds is None
            else _validate_timeout("timeout_seconds", timeout_seconds)
        )
        if decision_id in self._claimed:
            raise InvalidReviewDecisionError(
                f"decision_id already used: {decision_id!r}"
            )
        self._claim(decision_id)

        digest = _context_digest(context)
        try:
            self._emit(
                self._with_details(
                    AgentEvent.oversight_review_requested(
                        self._role, self._phase, decision_id=decision_id
                    ),
                    context_sha256=digest,
                )
            )
            requested_at = _now()
            decision = await self._await_queue(decision_id, context, timeout)
        except BaseException as exc:
            self._claimed.pop(decision_id, None)
            self._record_unresolved(decision_id, digest, exc)
            raise

        timed_out = decision is None
        if decision is None:
            decision = self._on_timeout_decision(decision_id)
        else:
            refusal = self._refusal(decision, requested_at, timeout)
            if refusal is not None:
                self._record_unresolved(
                    decision_id, digest, InvalidReviewDecisionError(refusal)
                )
                raise InvalidReviewDecisionError(f"{decision_id}: {refusal}")

        try:
            self._emit_resolved(
                decision_id,
                decision,
                context_sha256=digest,
                timed_out=timed_out,
                on_timeout=self._on_timeout if timed_out else None,
            )
        except BaseException as exc:
            # Unrecorded, so unresolved: a retry must be able to ask again, and the
            # log should show what this attempt decided if it can still be written.
            self._claimed.pop(decision_id, None)
            self._record_unresolved(
                decision_id,
                digest,
                exc,
                attempted_decision=decision.decision,
                reviewer_id=decision.reviewer.id,
            )
            raise
        if timed_out and self._on_timeout == "abort":
            raise ReviewTimeoutError(decision_id)
        return decision

    def _claim(self, decision_id: str) -> None:
        self._claimed[decision_id] = None
        while len(self._claimed) > _MAX_TRACKED_DECISIONS:
            self._claimed.popitem(last=False)

    async def _await_queue(
        self, decision_id: str, context: dict[str, Any], timeout: float
    ) -> ReviewDecision | None:
        """Call the queue under the gate's own deadline; expiry means ``None``."""
        deadline = asyncio.timeout(timeout)
        try:
            async with deadline:
                decision = await self._queue.enqueue(decision_id, context, timeout)
        except TimeoutError:
            if deadline.expired():
                return None
            raise
        if decision is not None and not isinstance(decision, ReviewDecision):
            raise TypeError("ReviewQueue.enqueue must return ReviewDecision or None")
        return decision

    @staticmethod
    def _refusal(
        decision: ReviewDecision, requested_at: datetime, timeout: float
    ) -> str | None:
        """Why this queue-supplied decision must not be honored, or ``None``."""
        reviewer = decision.reviewer
        if not isinstance(reviewer, ReviewerIdentity):
            return "the decision does not name a reviewer"
        try:
            reviewer_key = _match_key(reviewer.id, field_name="reviewer id")
        except (TypeError, ValueError):
            return "the reviewer id is not usable"
        if reviewer_key == SYSTEM_REVIEWER.id:
            return "the system reviewer cannot be supplied by a review queue"
        decided_at = decision.decided_at
        if not isinstance(decided_at, datetime) or decided_at.tzinfo is None:
            return "decided_at must be a timezone-aware datetime"
        earliest = requested_at - _DECISION_CLOCK_SKEW
        latest = requested_at + timedelta(seconds=timeout) + _DECISION_CLOCK_SKEW
        if not earliest <= decided_at <= latest:
            return f"decided_at {decided_at.isoformat()} is outside the window"
        return None

    def override(
        self,
        reason: str,
        reviewer: ReviewerIdentity,
        *,
        action: OverrideAction,
        decision_id: str = "manual-override",
    ) -> ReviewDecision:
        """Record a manual human override (Article 14(4)(d))."""
        if not reason.strip():
            raise ValueError("override reason must be non-empty")
        if action not in _VALID_OVERRIDE_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_VALID_OVERRIDE_ACTIONS)}: {action!r}"
            )
        outcome: ReviewOutcome = "reject" if action == "stop" else "modify"
        decision = ReviewDecision(
            decision=outcome,
            reason=reason,
            reviewer=reviewer,
            modifications={"action": action},
        )
        self._emit_resolved(decision_id, decision, action=action)
        return decision

    @staticmethod
    def _with_details(event: AgentEvent, **details: Any) -> AgentEvent:
        present = {key: value for key, value in details.items() if value is not None}
        return event.with_data({**(event.data or {}), **present})

    def _emit_resolved(
        self, decision_id: str, decision: ReviewDecision, **details: Any
    ) -> None:
        self._emit(
            self._with_details(
                AgentEvent.oversight_review_resolved(
                    self._role,
                    self._phase,
                    decision_id=decision_id,
                    decision=decision.decision,
                    reviewer_id=decision.reviewer.id,
                ),
                reason=decision.reason,
                modifications=decision.modifications,
                **details,
            )
        )

    def _record_unresolved(
        self, decision_id: str, digest: str, exc: BaseException, **details: Any
    ) -> None:
        """Close the trail for a review that ended without an honored decision.

        Never raises: this runs while another exception (possibly cancellation)
        is propagating, and that exception must win over an audit failure.
        """
        outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        try:
            self._emit(
                self._with_details(
                    AgentEvent.oversight_review_resolved(
                        self._role,
                        self._phase,
                        decision_id=decision_id,
                        decision=outcome,
                    ),
                    context_sha256=digest,
                    error_type=type(exc).__name__,
                    **details,
                    reason=(
                        str(exc)
                        if isinstance(exc, InvalidReviewDecisionError)
                        else None
                    ),
                )
            )
        except Exception as exc:
            logger.error(
                "could not record unresolved review %s: %s",
                decision_id,
                type(exc).__name__,
            )

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
