"""
Incident reporting — serious-incident detection + deadline tracking
(EU AI Act Articles 26 and 73).

Article 73 obliges providers to report *serious incidents* to the competent
authority, generally within 15 days of becoming aware. Article 26 obliges
deployers to monitor for malfunctions and serious incidents.

This module provides:

- :class:`IncidentReport` — a structured report with a computed 15-day
  :attr:`IncidentReport.reporting_deadline` and an :meth:`IncidentReport.overdue`
  check.
- :class:`SeriousIncidentDetector` — composable rule set over ``AgentEvent``\\s;
  ships a default detector that classifies governance breaches and terminal
  failures as ``MALFUNCTION``, and accepts caller rules that escalate to
  ``SERIOUS``.
- :class:`IncidentReportingSink` — an ``EventSink`` that runs the detector on
  every event and materializes an :class:`IncidentReport` when one fires,
  optionally mirroring it to the audit log.

Architecture note: this is an **EventSink**, not a Hook — incidents are derived
from ``AgentEvent``\\s (including ``governance.breach`` and ``agent.failed``),
which flow through the event sink, not the hook chain.

.. warning::

    Engineering primitive, not legal advice. What counts as a "serious incident"
    and the exact reporting deadline are legal determinations for the deployer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from techrevati.runtime.agent_events import AgentEvent, AgentEventName
from techrevati.runtime.compliance.audit_log import AuditLogSink
from techrevati.runtime.compliance.human_oversight import ReviewerIdentity

__all__ = [
    "IncidentReport",
    "IncidentReportingSink",
    "IncidentSeverity",
    "SeriousIncidentDetector",
]

#: Article 73(2) — serious incidents are reported within 15 days of awareness.
REPORTING_WINDOW = timedelta(days=15)

EventDetector = Callable[[AgentEvent], "IncidentSeverity | None"]


def _now() -> datetime:
    return datetime.now(UTC)


class IncidentSeverity(str, Enum):
    """Incident severity. ``SERIOUS`` triggers the Article 73 reporting clock."""

    MALFUNCTION = "malfunction"
    SERIOUS = "serious"


_SEVERITY_RANK = {IncidentSeverity.MALFUNCTION: 1, IncidentSeverity.SERIOUS: 2}


@dataclass(frozen=True)
class IncidentReport:
    """A structured incident record with a 15-day reporting deadline (Article 73)."""

    id: str
    detected_at: datetime
    severity: IncidentSeverity
    description: str
    affected_systems: tuple[str, ...] = ()
    affected_persons: int | None = None
    initial_cause: str = ""
    reporter: ReviewerIdentity | None = None

    def __post_init__(self) -> None:
        if self.detected_at.tzinfo is None:
            raise ValueError("detected_at must be timezone-aware (UTC)")

    @property
    def reporting_deadline(self) -> datetime:
        return self.detected_at + REPORTING_WINDOW

    def overdue(self, *, now: datetime | None = None) -> bool:
        return (now or _now()) > self.reporting_deadline

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "detected_at": self.detected_at.isoformat(),
            "severity": self.severity.value,
            "description": self.description,
            "affected_systems": list(self.affected_systems),
            "affected_persons": self.affected_persons,
            "initial_cause": self.initial_cause,
            "reporting_deadline": self.reporting_deadline.isoformat(),
            "reporter": self.reporter.to_dict() if self.reporter else None,
        }


def _default_detector(event: AgentEvent) -> IncidentSeverity | None:
    """Classify governance breaches and terminal failures as MALFUNCTION."""
    if event.event is AgentEventName.GOVERNANCE_BREACH:
        return IncidentSeverity.MALFUNCTION
    if event.event is AgentEventName.AGENT_FAILED or event.failure_class is not None:
        return IncidentSeverity.MALFUNCTION
    return None


class SeriousIncidentDetector:
    """Composable rule set mapping ``AgentEvent``\\s to an :class:`IncidentSeverity`.

    Ships one default rule (governance breach / terminal failure → MALFUNCTION).
    Add caller rules to escalate domain-specific events to SERIOUS.
    """

    def __init__(self, *, include_default: bool = True) -> None:
        self._detectors: list[EventDetector] = []
        if include_default:
            self._detectors.append(_default_detector)

    def add(self, detector: EventDetector) -> None:
        self._detectors.append(detector)

    def check(self, event: AgentEvent) -> IncidentSeverity | None:
        """Return the highest severity any rule assigns to ``event`` (or None)."""
        best: IncidentSeverity | None = None
        for detector in self._detectors:
            severity = detector(event)
            if severity is None:
                continue
            if best is None or _SEVERITY_RANK[severity] > _SEVERITY_RANK[best]:
                best = severity
        return best


@dataclass
class IncidentReportingSink:
    """``EventSink`` that materializes :class:`IncidentReport`\\s from events.

    On each event it runs ``detector.check``; if a severity fires it builds a
    report (mirrored to ``audit_log`` when set, and passed to ``on_incident``).
    """

    detector: SeriousIncidentDetector
    audit_log: AuditLogSink | None = None
    on_incident: Callable[[IncidentReport], None] | None = None
    reporter: ReviewerIdentity | None = None
    _incidents: list[IncidentReport] = field(
        default_factory=list, init=False, repr=False
    )
    _counter: int = field(default=0, init=False, repr=False)

    @property
    def incidents(self) -> list[IncidentReport]:
        return list(self._incidents)

    def emit(self, event: AgentEvent) -> None:
        if not isinstance(event, AgentEvent):
            raise TypeError("event must be an AgentEvent")
        severity = self.detector.check(event)
        if severity is None:
            return
        self._counter += 1
        report = IncidentReport(
            id=f"incident-{self._counter}",
            detected_at=_now(),
            severity=severity,
            description=event.detail or event.event.value,
            affected_systems=((event.role,) if event.role else ()),
            initial_cause=(
                event.failure_class.value if event.failure_class else event.event.value
            ),
            reporter=self.reporter,
        )
        self._incidents.append(report)
        if self.audit_log is not None:
            self.audit_log.emit(
                AgentEvent(
                    event=AgentEventName.AGENT_BLOCKED,
                    status=event.status,
                    role=event.role,
                    phase=event.phase,
                    detail=f"incident {report.id} ({severity.value})",
                    data={"incident": report.to_dict()},
                )
            )
        if self.on_incident is not None:
            self.on_incident(report)
