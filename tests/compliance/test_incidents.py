"""Tests for incident reporting (EU AI Act Articles 26 + 73)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from techrevati.runtime import AgentEvent
from techrevati.runtime.compliance import (
    AuditLogSink,
    IncidentReport,
    IncidentReportingSink,
    IncidentSeverity,
    InMemoryAuditBackend,
    SeriousIncidentDetector,
)


def test_reporting_deadline_is_15_days() -> None:
    detected = datetime(2026, 6, 1, tzinfo=UTC)
    report = IncidentReport(
        id="i1",
        detected_at=detected,
        severity=IncidentSeverity.SERIOUS,
        description="data leak",
    )
    assert report.reporting_deadline == detected + timedelta(days=15)
    assert not report.overdue(now=detected + timedelta(days=10))
    assert report.overdue(now=detected + timedelta(days=16))


def test_detected_at_must_be_aware() -> None:
    with pytest.raises(ValueError):
        IncidentReport(
            id="i1",
            detected_at=datetime(2026, 6, 1),  # naive
            severity=IncidentSeverity.MALFUNCTION,
            description="x",
        )


def test_default_detector_flags_governance_breach() -> None:
    detector = SeriousIncidentDetector()
    breach = AgentEvent.governance_breach(
        "agent",
        "run",
        limit_name="max_cost",
        observed=2.0,
        ceiling=1.0,
        scope="session",
    )
    assert detector.check(breach) is IncidentSeverity.MALFUNCTION


def test_default_detector_ignores_normal_events() -> None:
    detector = SeriousIncidentDetector()
    assert detector.check(AgentEvent.started("a", "p")) is None


def test_custom_rule_escalates_to_serious() -> None:
    detector = SeriousIncidentDetector()

    def rule(event: AgentEvent) -> IncidentSeverity | None:
        if event.detail and "fundamental rights" in event.detail:
            return IncidentSeverity.SERIOUS
        return None

    detector.add(rule)
    event = AgentEvent.blocked("a", "p", detail="fundamental rights breach")
    # default would say MALFUNCTION? blocked has no failure_class -> None by default;
    # custom rule escalates to SERIOUS (the max wins).
    assert detector.check(event) is IncidentSeverity.SERIOUS


def test_serious_outranks_malfunction() -> None:
    detector = SeriousIncidentDetector()
    detector.add(lambda e: IncidentSeverity.SERIOUS)
    failed = AgentEvent.governance_breach(
        "a", "p", limit_name="x", observed=1.0, ceiling=0.0, scope="session"
    )
    assert detector.check(failed) is IncidentSeverity.SERIOUS


def test_sink_materializes_incident_and_mirrors_to_audit() -> None:
    audit = AuditLogSink(InMemoryAuditBackend())
    captured: list[IncidentReport] = []
    sink = IncidentReportingSink(
        SeriousIncidentDetector(), audit_log=audit, on_incident=captured.append
    )
    sink.emit(AgentEvent.started("a", "p"))  # ignored
    sink.emit(
        AgentEvent.governance_breach(
            "loan",
            "decide",
            limit_name="cost",
            observed=5.0,
            ceiling=1.0,
            scope="session",
        )
    )
    assert len(sink.incidents) == 1
    assert captured[0].severity is IncidentSeverity.MALFUNCTION
    assert sink.incidents[0].affected_systems == ("loan",)
    # Audit mirror present + chain intact.
    assert any("incident" in (r.payload.get("detail") or "") for r in audit.records())
    assert audit.verify_chain().valid


def test_incident_to_dict_shape() -> None:
    report = IncidentReport(
        id="i1",
        detected_at=datetime(2026, 6, 1, tzinfo=UTC),
        severity=IncidentSeverity.SERIOUS,
        description="x",
        affected_persons=3,
    )
    d = report.to_dict()
    assert d["severity"] == "serious"
    assert d["affected_persons"] == 3
    assert "reporting_deadline" in d
