"""Tests for agent_patterns.agent_events"""

import json
from techrevati.runtime.agent_events import (
    AgentEvent, AgentEventName, AgentEventStatus, AgentFailureClass,
)


def test_event_names_serialize():
    assert AgentEventName.AGENT_STARTED.value == "agent.started"
    assert AgentEventName.PHASE_GATE_FAILED.value == "phase.gate_failed"
    assert AgentEventName.RECOVERY_PROVIDER_SWITCHED.value == "agent.recovery.provider_switched"


def test_failure_classes_complete():
    assert len(AgentFailureClass) == 10
    assert AgentFailureClass.LLM_TIMEOUT.value == "llm_timeout"
    assert AgentFailureClass.UNKNOWN.value == "unknown"


def test_started_constructor():
    e = AgentEvent.started("writer", "draft")
    assert e.event == AgentEventName.AGENT_STARTED
    assert e.status == AgentEventStatus.RUNNING
    assert e.role == "writer"
    assert e.phase == "draft"


def test_failed_includes_failure_class():
    e = AgentEvent.failed("writer", "draft", AgentFailureClass.LLM_TIMEOUT, "30s")
    d = e.to_dict()
    assert d["failure_class"] == "llm_timeout"
    assert d["detail"] == "30s"


def test_completed_constructor():
    e = AgentEvent.completed("reviewer", "review", detail="confidence=0.92")
    assert e.status == AgentEventStatus.COMPLETED
    assert e.detail == "confidence=0.92"


def test_builder_pattern():
    e = (AgentEvent.started("writer", "draft")
         .with_failure_class(AgentFailureClass.TOOL_ERROR)
         .with_detail("file not found")
         .with_data({"file": "main.py"})
         .with_project(42))
    assert e.failure_class == AgentFailureClass.TOOL_ERROR
    assert e.detail == "file not found"
    assert e.data == {"file": "main.py"}
    assert e.project_id == 42


def test_to_dict_backward_compat():
    e = AgentEvent.started("writer", "draft")
    d = e.to_dict()
    assert "event" in d  # new format
    assert "type" in d   # backward compat
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
    orig = (AgentEvent.started("reviewer", "review")
            .with_failure_class(AgentFailureClass.CONTEXT_OVERFLOW)
            .with_data({"tokens_used": 128000}))
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
    e = AgentEvent.failed("writer", "release", AgentFailureClass.LLM_TIMEOUT, "30 second timeout")
    attrs = e.to_otel_attributes()
    assert attrs["agent.event"] == "agent.failed"
    assert attrs["agent.event.status"] == "failed"
    assert attrs["agent.failure_class"] == "llm_timeout"
    assert attrs["agent.detail"] == "30 second timeout"


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
