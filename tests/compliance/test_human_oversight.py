"""Tests for human oversight (EU AI Act Article 14)."""

from __future__ import annotations

import pytest

from techrevati.runtime import AgentEvent
from techrevati.runtime.compliance import (
    AuditLogSink,
    ExplanationReport,
    HumanOversightInterface,
    InMemoryAuditBackend,
    ReviewDecision,
    ReviewerIdentity,
    ReviewTimeoutError,
    StaticReviewQueue,
)

REVIEWER = ReviewerIdentity(
    id="alice@corp", role="approver", authentication_method="oauth"
)


@pytest.mark.asyncio
async def test_pause_resume_returns_human_decision() -> None:
    audit = AuditLogSink(InMemoryAuditBackend())
    decision = ReviewDecision(
        decision="approve", reason="looks fine", reviewer=REVIEWER
    )
    queue = StaticReviewQueue({"d1": decision})
    oversight = HumanOversightInterface(queue, audit_log=audit)

    result = await oversight.pause_for_review("d1", {"prompt": "transfer $1000"})
    assert result.decision == "approve"
    assert result.reviewer.id == "alice@corp"
    assert queue.requested == ["d1"]

    # Audit trail records request + resolution with reviewer identity.
    types = [r.event_type for r in audit.records()]
    assert "oversight.review_requested" in types
    resolved = [
        r for r in audit.records() if r.event_type == "oversight.review_resolved"
    ]
    assert resolved[0].payload["reviewer_id"] == "alice@corp"
    assert audit.verify_chain().valid


@pytest.mark.asyncio
async def test_pause_for_review_is_standalone_no_lifecycle_side_effects() -> None:
    # Honest contract (Article 14 docs): the interface is a standalone async gate.
    # Its only effect is the two oversight.* audit events plus the returned
    # decision — it holds no worker reference and does NOT transition any worker
    # lifecycle status. A future change that wires it into a session must update
    # this test (and the docs) deliberately.
    audit = AuditLogSink(InMemoryAuditBackend())
    queue = StaticReviewQueue(
        {"d1": ReviewDecision(decision="approve", reason="ok", reviewer=REVIEWER)}
    )
    oversight = HumanOversightInterface(queue, audit_log=audit)

    await oversight.pause_for_review("d1", {})

    event_types = [r.event_type for r in audit.records()]
    assert event_types == [
        "oversight.review_requested",
        "oversight.review_resolved",
    ]
    assert not any(t.startswith("agent.") or "status" in t for t in event_types)


@pytest.mark.asyncio
async def test_timeout_abort_raises() -> None:
    queue = StaticReviewQueue({})  # never resolves -> timeout
    oversight = HumanOversightInterface(queue, on_timeout="abort")
    with pytest.raises(ReviewTimeoutError):
        await oversight.pause_for_review("d1", {})


@pytest.mark.asyncio
async def test_timeout_proceed_with_warning_returns_approval() -> None:
    audit = AuditLogSink(InMemoryAuditBackend())
    queue = StaticReviewQueue({})
    oversight = HumanOversightInterface(
        queue, on_timeout="proceed_with_warning", audit_log=audit
    )
    result = await oversight.pause_for_review("d1", {})
    assert result.decision == "approve"
    assert result.reviewer.id == "system"
    # Resolution still recorded.
    assert any(r.event_type == "oversight.review_resolved" for r in audit.records())


@pytest.mark.asyncio
async def test_reject_decision_is_returned() -> None:
    queue = StaticReviewQueue(
        {"d1": ReviewDecision(decision="reject", reason="too risky", reviewer=REVIEWER)}
    )
    oversight = HumanOversightInterface(queue)
    result = await oversight.pause_for_review("d1", {})
    assert result.decision == "reject"


def test_override_records_decision() -> None:
    audit = AuditLogSink(InMemoryAuditBackend())
    oversight = HumanOversightInterface(StaticReviewQueue(), audit_log=audit)
    decision = oversight.override("manual stop", REVIEWER, action="stop")
    assert decision.decision == "reject"
    assert decision.modifications == {"action": "stop"}
    assert any(r.event_type == "oversight.review_resolved" for r in audit.records())


def test_requires_review_matches_configured_events() -> None:
    oversight = HumanOversightInterface(
        StaticReviewQueue(), require_review_for=("governance.breach",)
    )
    assert oversight.requires_review("governance.breach")
    assert not oversight.requires_review("agent.started")


def test_invalid_on_timeout_rejected() -> None:
    with pytest.raises(ValueError):
        HumanOversightInterface(StaticReviewQueue(), on_timeout="explode")  # type: ignore[arg-type]


def test_reviewer_identity_validation() -> None:
    with pytest.raises(ValueError):
        ReviewerIdentity(id="", role="approver")
    with pytest.raises(ValueError):
        ReviewerIdentity(id="x", role="")


def test_explanation_report_from_events() -> None:
    events = [
        AgentEvent.started("planner", "design"),
        AgentEvent.tool_called("planner", "design", "search"),
        AgentEvent.tool_completed("planner", "design", "search"),
        AgentEvent.completed("planner", "design"),
    ]
    report = ExplanationReport.from_events("turn-1", events)
    assert report.role == "planner"
    assert report.tools_invoked == ("search",)
    assert report.event_count == 4
    assert report.final_status == "completed"
    md = report.to_markdown()
    assert "turn-1" in md
    assert "search" in md
