"""Tests for agent_patterns.agent_events"""

import json
from dataclasses import replace
from typing import Any, cast

import pytest

from techrevati.runtime.agent_events import (
    AgentEvent,
    AgentEventName,
    AgentEventStatus,
    AgentFailureClass,
)


def test_event_names_serialize():
    assert AgentEventName.AGENT_STARTED.value == "agent.started"
    assert AgentEventName.PHASE_GATE_FAILED.value == "phase.gate_failed"
    assert (
        AgentEventName.RECOVERY_PROVIDER_SWITCHED.value
        == "agent.recovery.provider_switched"
    )


def test_failure_classes_complete():
    assert len(AgentFailureClass) == 14
    assert AgentFailureClass.LLM_TIMEOUT.value == "llm_timeout"
    assert AgentFailureClass.GOVERNANCE_BREACH.value == "governance_breach"
    assert AgentFailureClass.PERMISSION_DENIED.value == "permission_denied"
    assert AgentFailureClass.GUARDRAIL_VIOLATION.value == "guardrail_violation"
    assert AgentFailureClass.CANCELLED.value == "cancelled"
    assert AgentFailureClass.UNKNOWN.value == "unknown"


def test_cancelled_failure_uses_cancelled_status():
    event = AgentEvent.failed(
        "writer",
        "draft",
        AgentFailureClass.CANCELLED,
        detail="user requested cancellation",
    )

    assert event.event == AgentEventName.AGENT_FAILED
    assert event.status == AgentEventStatus.CANCELLED
    assert event.failure_class == AgentFailureClass.CANCELLED
    assert event.to_dict()["status"] == "cancelled"


def test_cancelled_failure_rejects_failed_status():
    with pytest.raises(ValueError, match="status cancelled"):
        AgentEvent(
            event=AgentEventName.AGENT_FAILED,
            status=AgentEventStatus.FAILED,
            failure_class=AgentFailureClass.CANCELLED,
        )


def test_cancelled_status_requires_cancelled_failure_class():
    with pytest.raises(ValueError, match="failure_class cancelled"):
        AgentEvent(
            event=AgentEventName.AGENT_FAILED,
            status=AgentEventStatus.CANCELLED,
            failure_class=AgentFailureClass.LLM_ERROR,
        )


def test_failed_event_requires_failure_class():
    with pytest.raises(ValueError, match="require failure_class"):
        AgentEvent(
            event=AgentEventName.AGENT_FAILED,
            status=AgentEventStatus.FAILED,
        )


def test_started_constructor():
    e = AgentEvent.started("writer", "draft")
    assert e.event == AgentEventName.AGENT_STARTED
    assert e.status == AgentEventStatus.RUNNING
    assert e.role == "writer"
    assert e.phase == "draft"


def test_ready_constructor_carries_metadata_only():
    e = AgentEvent.ready(
        "writer",
        "draft",
        detail="input received",
        data={"kind": "human_input"},
    )

    assert e.event == AgentEventName.AGENT_READY
    assert e.status == AgentEventStatus.READY
    assert e.data == {"kind": "human_input"}


def test_failed_includes_failure_class():
    e = AgentEvent.failed("writer", "draft", AgentFailureClass.LLM_TIMEOUT, "30s")
    d = e.to_dict()
    assert d["failure_class"] == "llm_timeout"
    assert d["detail"] == "30s"


def test_completed_constructor():
    e = AgentEvent.completed("reviewer", "review", detail="confidence=0.92")
    assert e.status == AgentEventStatus.COMPLETED
    assert e.detail == "confidence=0.92"


def test_blocked_constructor_carries_metadata_only():
    e = AgentEvent.blocked(
        "writer",
        "draft",
        detail="permission blocked tool call",
        data={"tool": "write_db", "kind": "permission"},
    )

    assert e.event == AgentEventName.AGENT_BLOCKED
    assert e.status == AgentEventStatus.BLOCKED
    assert e.data == {"tool": "write_db", "kind": "permission"}


def test_tool_event_constructors_do_not_capture_tool_output():
    called = AgentEvent.tool_called("writer", "draft", "lookup")
    completed = AgentEvent.tool_completed("writer", "draft", "lookup")

    assert called.event == AgentEventName.AGENT_TOOL_CALLED
    assert called.status == AgentEventStatus.RUNNING
    assert called.data == {"tool": "lookup"}
    assert completed.event == AgentEventName.AGENT_TOOL_COMPLETED
    assert completed.status == AgentEventStatus.COMPLETED
    assert completed.data == {"tool": "lookup"}


def test_recovery_outcome_constructors_carry_structured_metadata():
    data = {"scenario": "llm_timeout", "outcome": "recovered", "steps_taken": 1}
    succeeded = AgentEvent.recovery_succeeded(
        "writer", "draft", detail="llm_timeout: recovered", data=data
    )
    failed = AgentEvent.recovery_failed(
        "writer", "draft", detail="llm_timeout: partial_recovery", data=data
    )
    escalated = AgentEvent.recovery_escalated(
        "writer", "draft", detail="llm_timeout: escalation_required", data=data
    )

    assert succeeded.event == AgentEventName.RECOVERY_SUCCEEDED
    assert succeeded.status == AgentEventStatus.RUNNING
    assert failed.event == AgentEventName.RECOVERY_FAILED
    assert failed.status == AgentEventStatus.FAILED
    assert escalated.event == AgentEventName.RECOVERY_ESCALATED
    assert escalated.status == AgentEventStatus.FAILED
    assert succeeded.data == data


def test_builder_pattern():
    e = (
        AgentEvent.started("writer", "draft")
        .with_failure_class(AgentFailureClass.TOOL_ERROR)
        .with_detail("file not found")
        .with_data({"file": "main.py"})
        .with_project(42)
    )
    assert e.failure_class == AgentFailureClass.TOOL_ERROR
    assert e.detail == "file not found"
    assert e.data == {"file": "main.py"}
    assert e.project_id == 42


def test_event_rejects_invalid_shape():
    with pytest.raises(ValueError, match="valid AgentEventName"):
        AgentEvent(event=cast(Any, "bad.event"), status=AgentEventStatus.RUNNING)
    with pytest.raises(ValueError, match="valid AgentEventStatus"):
        AgentEvent(event=AgentEventName.AGENT_STARTED, status=cast(Any, "bad"))
    with pytest.raises(ValueError, match="role"):
        AgentEvent.started("", "draft")
    with pytest.raises(ValueError, match="phase"):
        AgentEvent.started("writer", " ")
    with pytest.raises(TypeError, match="project_id"):
        AgentEvent.started("writer", "draft").with_project(cast(Any, True))
    with pytest.raises(ValueError, match="project_id"):
        AgentEvent.started("writer", "draft").with_project(-1)
    with pytest.raises(TypeError, match="failure_class"):
        AgentEvent.started("writer", "draft").with_failure_class(cast(Any, object()))
    with pytest.raises(TypeError, match="data"):
        AgentEvent.started("writer", "draft").with_data(cast(Any, []))
    with pytest.raises(TypeError, match="data keys"):
        AgentEvent.started("writer", "draft").with_data(cast(Any, {1: "bad"}))
    with pytest.raises(ValueError, match="emitted_at"):
        AgentEvent(
            event=AgentEventName.AGENT_STARTED,
            status=AgentEventStatus.RUNNING,
            emitted_at="",
        )


def test_event_copies_data_on_builders_and_serialization():
    data: dict[str, Any] = {"file": "main.py", "meta": {"tags": ["initial"]}}
    e = AgentEvent.started("writer", "draft").with_data(data)
    data["later"] = True
    data["meta"]["tags"].append("changed")
    assert e.data == {"file": "main.py", "meta": {"tags": ["initial"]}}

    d = e.to_dict()
    d["data"]["extra"] = True
    d["data"]["meta"]["tags"].append("serialized-change")
    assert e.data == {"file": "main.py", "meta": {"tags": ["initial"]}}


def test_event_constructor_data_is_deep_copied():
    data: dict[str, Any] = {"meta": {"attempts": [1]}}
    e = AgentEvent.ready("writer", "draft", data=data)
    data["meta"]["attempts"].append(2)

    assert e.data == {"meta": {"attempts": [1]}}

    restored = AgentEvent.from_dict(e.to_dict())
    assert restored.data == {"meta": {"attempts": [1]}}


def test_to_dict_backward_compat():
    e = AgentEvent.started("writer", "draft")
    d = e.to_dict()
    assert "event" in d  # new format
    assert "type" in d  # backward compat
    assert d["event"] == "agent.started"
    assert d["type"] == "started"


def test_to_dict_is_json_serializable():
    e = AgentEvent.failed("writer", "draft", AgentFailureClass.LLM_ERROR, "bad request")
    j = json.dumps(e.to_dict())
    assert "llm_error" in j


def test_optional_fields_excluded_when_none():
    e = AgentEvent.phase_started("draft")
    d = e.to_dict()
    assert "role" not in d
    assert "failure_class" not in d
    assert "detail" not in d
    assert "data" not in d


def test_gate_events():
    passed = AgentEvent.gate_passed("draft", "score=92%")
    assert passed.status == AgentEventStatus.GREEN
    failed = AgentEvent.gate_failed("draft", "score=78%")
    assert failed.status == AgentEventStatus.RED


def test_from_dict_roundtrip_basic():
    """Test dict serialization round-trip for basic event."""
    orig = AgentEvent.started("writer", "draft")
    d = orig.to_dict()
    restored = AgentEvent.from_dict(d)
    assert restored.event == orig.event
    assert restored.status == orig.status
    assert restored.role == orig.role
    assert restored.phase == orig.phase


def test_from_dict_roundtrip_with_failure():
    """Test dict serialization round-trip for failed event."""
    orig = AgentEvent.failed("writer", "draft", AgentFailureClass.LLM_TIMEOUT, "30s")
    orig = orig.with_project(42).with_data({"error_code": 500})
    d = orig.to_dict()
    restored = AgentEvent.from_dict(d)
    assert restored.event == orig.event
    assert restored.failure_class == AgentFailureClass.LLM_TIMEOUT
    assert restored.detail == "30s"
    assert restored.project_id == 42
    assert restored.data == {"error_code": 500}


def test_from_json_roundtrip():
    """Test JSON serialization round-trip."""
    orig = (
        AgentEvent.started("reviewer", "review")
        .with_failure_class(AgentFailureClass.CONTEXT_OVERFLOW)
        .with_data({"tokens_used": 128000})
    )
    json_str = orig.to_json()
    restored = AgentEvent.from_json(json_str)
    assert restored.event == orig.event
    assert restored.role == orig.role
    assert restored.phase == orig.phase
    assert restored.failure_class == orig.failure_class
    assert restored.data == orig.data


def test_from_dict_all_optional_fields():
    """Test from_dict with all optional fields populated."""
    d = {
        "event": "agent.failed",
        "status": "failed",
        "emitted_at": "2026-04-08T10:00:00+00:00",
        "role": "writer",
        "phase": "release",
        "project_id": 99,
        "failure_class": "rate_limit",
        "detail": "quota exceeded",
        "data": {"retry_after_sec": 120},
    }
    e = AgentEvent.from_dict(d)
    assert e.event == AgentEventName.AGENT_FAILED
    assert e.status == AgentEventStatus.FAILED
    assert e.role == "writer"
    assert e.phase == "release"
    assert e.project_id == 99
    assert e.failure_class == AgentFailureClass.RATE_LIMIT
    assert e.detail == "quota exceeded"
    assert e.data == {"retry_after_sec": 120}


def test_from_dict_rejects_inconsistent_cancelled_failure_status():
    with pytest.raises(ValueError, match="status cancelled"):
        AgentEvent.from_dict(
            {
                "event": "agent.failed",
                "status": "failed",
                "failure_class": "cancelled",
            }
        )


def test_from_dict_rejects_failed_event_without_failure_class():
    with pytest.raises(ValueError, match="require failure_class"):
        AgentEvent.from_dict(
            {
                "event": "agent.failed",
                "status": "failed",
            }
        )


def test_from_dict_minimal():
    """Test from_dict with only required fields."""
    d = {
        "event": "phase.started",
        "status": "running",
        "emitted_at": "2026-04-08T10:00:00+00:00",
    }
    e = AgentEvent.from_dict(d)
    assert e.event == AgentEventName.PHASE_STARTED
    assert e.status == AgentEventStatus.RUNNING
    assert e.role is None
    assert e.phase is None
    assert e.failure_class is None


def test_from_dict_rejects_invalid_payload_shape():
    with pytest.raises(TypeError, match="data must be a dict"):
        AgentEvent.from_dict(cast(Any, []))
    with pytest.raises(TypeError, match="JSON payload"):
        AgentEvent.from_json("[1, 2, 3]")
    with pytest.raises(TypeError, match="project_id"):
        AgentEvent.from_dict(
            {"event": "agent.started", "status": "running", "project_id": True}
        )
    with pytest.raises(TypeError, match="data"):
        AgentEvent.from_dict(
            {"event": "agent.started", "status": "running", "data": []}
        )


def test_governance_event_constructors_validate_payload():
    event = AgentEvent.governance_breach(
        "r",
        "p",
        limit_name="max_iterations",
        observed=3.0,
        ceiling=2.0,
        scope="session",
    )
    assert event.failure_class == AgentFailureClass.GOVERNANCE_BREACH
    with pytest.raises(ValueError, match="observed"):
        AgentEvent.governance_breach(
            "r",
            "p",
            limit_name="max_cost",
            observed=float("nan"),
            ceiling=1.0,
            scope="session",
        )
    with pytest.raises(ValueError, match="limit_name"):
        AgentEvent.governance_alert(
            "r",
            "p",
            limit_name="",
            observed=2.0,
            ceiling=1.0,
            scope="session",
        )


def test_to_otel_attributes_basic():
    """Test OpenTelemetry attribute conversion."""
    e = AgentEvent.started("writer", "draft")
    attrs = e.to_otel_attributes()
    assert attrs["agent.event"] == "agent.started"
    assert attrs["agent.event.status"] == "running"
    assert attrs["agent.role"] == "writer"
    assert attrs["agent.phase"] == "draft"
    assert "agent.event.timestamp" in attrs


def test_to_otel_attributes_with_failure():
    """Test OTEL attributes for failed event."""
    e = AgentEvent.failed(
        "writer", "release", AgentFailureClass.LLM_TIMEOUT, "30 second timeout"
    )
    attrs = e.to_otel_attributes()
    assert attrs["agent.event"] == "agent.failed"
    assert attrs["agent.event.status"] == "failed"
    assert attrs["agent.failure_class"] == "llm_timeout"
    assert attrs["agent.detail"] == "30 second timeout"


def test_to_otel_attributes_with_cancelled_failure():
    """Cancelled terminal events remain typed without claiming failure status."""
    e = AgentEvent.failed("writer", "release", AgentFailureClass.CANCELLED)
    attrs = e.to_otel_attributes()
    assert attrs["agent.event"] == "agent.failed"
    assert attrs["agent.event.status"] == "cancelled"
    assert attrs["agent.failure_class"] == "cancelled"


def test_to_otel_attributes_with_project():
    """Test OTEL attributes include project_id."""
    e = AgentEvent.started("reviewer", "review").with_project(42)
    attrs = e.to_otel_attributes()
    assert attrs["agent.project_id"] == 42


def test_to_otel_attributes_excludes_none_fields():
    """Test that optional fields are excluded when None."""
    e = AgentEvent.phase_started("draft")
    attrs = e.to_otel_attributes()
    assert "agent.role" not in attrs
    assert "agent.failure_class" not in attrs
    assert "agent.detail" not in attrs


# Golden wire-format snapshots. to_dict() is the frozen serialization contract
# consumed by durable sinks and the OTel bridge, so a field rename, addition, or
# removal must break a test on purpose. emitted_at is pinned for determinism.
FIXED_TS = "2026-01-01T00:00:00+00:00"

GOLDEN_TO_DICT: list[tuple[AgentEvent, dict[str, Any]]] = [
    (
        AgentEvent.started("planner", "draft"),
        {
            "event": "agent.started",
            "type": "started",
            "status": "running",
            "emitted_at": FIXED_TS,
            "role": "planner",
            "phase": "draft",
        },
    ),
    (
        AgentEvent.completed("planner", "draft", "done"),
        {
            "event": "agent.completed",
            "type": "completed",
            "status": "completed",
            "emitted_at": FIXED_TS,
            "role": "planner",
            "phase": "draft",
            "detail": "done",
        },
    ),
    (
        AgentEvent.tool_called("planner", "draft", "search"),
        {
            "event": "agent.tool_called",
            "type": "tool_called",
            "status": "running",
            "emitted_at": FIXED_TS,
            "role": "planner",
            "phase": "draft",
            "data": {"tool": "search"},
        },
    ),
    (
        AgentEvent.failed("planner", "draft", AgentFailureClass.TOOL_ERROR, "boom"),
        {
            "event": "agent.failed",
            "type": "failed",
            "status": "failed",
            "emitted_at": FIXED_TS,
            "role": "planner",
            "phase": "draft",
            "failure_class": "tool_error",
            "detail": "boom",
        },
    ),
    (
        AgentEvent.gate_passed("review"),
        {
            "event": "phase.gate_passed",
            "type": "gate_passed",
            "status": "green",
            "emitted_at": FIXED_TS,
            "phase": "review",
        },
    ),
    (
        AgentEvent.phase_started("draft"),
        {
            "event": "phase.started",
            "type": "started",
            "status": "running",
            "emitted_at": FIXED_TS,
            "phase": "draft",
        },
    ),
    (
        AgentEvent.oversight_review_resolved(
            "approver",
            "decide",
            decision_id="d1",
            decision="approve",
            reviewer_id="alice",
        ),
        {
            "event": "oversight.review_resolved",
            "type": "review_resolved",
            "status": "running",
            "emitted_at": FIXED_TS,
            "role": "approver",
            "phase": "decide",
            "detail": "review approve: d1",
            "data": {
                "decision_id": "d1",
                "decision": "approve",
                "reviewer_id": "alice",
            },
        },
    ),
    (
        AgentEvent.started("planner", "draft").with_project(42),
        {
            "event": "agent.started",
            "type": "started",
            "status": "running",
            "emitted_at": FIXED_TS,
            "role": "planner",
            "phase": "draft",
            "project_id": 42,
        },
    ),
]


@pytest.mark.parametrize("event, expected", GOLDEN_TO_DICT)
def test_to_dict_golden_snapshot(event: AgentEvent, expected: dict[str, Any]) -> None:
    pinned = replace(event, emitted_at=FIXED_TS)
    assert pinned.to_dict() == expected
    # The wire contract round-trips losslessly.
    assert AgentEvent.from_dict(pinned.to_dict()) == pinned
