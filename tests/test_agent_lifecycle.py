"""Tests for agent_patterns.agent_lifecycle"""

import threading
from typing import Any, cast

import pytest

from techrevati.runtime.agent_lifecycle import (
    AgentRegistry,
    AgentStatus,
    AgentWorker,
    AgentWorkerEvent,
    InvalidTransitionError,
)


def test_create_worker_starts_idle():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    assert w.status == AgentStatus.IDLE
    assert w.role == "writer"
    assert w.phase == "draft"
    assert len(w.events) == 0


def test_create_worker_rejects_invalid_identity():
    reg = AgentRegistry()
    with pytest.raises(ValueError, match="role"):
        reg.create("", "draft")
    with pytest.raises(ValueError, match="phase"):
        reg.create("writer", " ")
    with pytest.raises(TypeError, match="role"):
        reg.create(cast(Any, 123), "draft")


def test_create_worker_rejects_invalid_project_id():
    reg = AgentRegistry()
    with pytest.raises(TypeError, match="project_id"):
        reg.create("writer", "draft", project_id=cast(Any, True))
    with pytest.raises(ValueError, match="project_id"):
        reg.create("writer", "draft", project_id=-1)


def test_valid_transition_sequence():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    reg.transition(w.worker_id, AgentStatus.INITIALIZING, "building prompt")
    reg.transition(w.worker_id, AgentStatus.RUNNING, "calling LLM")
    reg.transition(w.worker_id, AgentStatus.COMPLETED, "done")
    assert w.status == AgentStatus.COMPLETED
    assert len(w.events) == 3


def test_invalid_transition_raises():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    with pytest.raises(InvalidTransitionError):
        reg.transition(w.worker_id, AgentStatus.COMPLETED)  # IDLE -> COMPLETED invalid


def test_transition_accepts_status_values_and_validates_detail():
    w = AgentWorker(worker_id="test", role="writer", phase="f")
    event = w.transition("initializing")
    assert w.status == AgentStatus.INITIALIZING
    assert event.status == "initializing"

    with pytest.raises(TypeError, match="detail"):
        w.transition(AgentStatus.RUNNING, detail=cast(Any, object()))
    with pytest.raises(ValueError, match="valid AgentStatus"):
        w.transition("unknown")


def test_terminal_state_blocks_transitions():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    reg.transition(w.worker_id, AgentStatus.INITIALIZING)
    reg.transition(w.worker_id, AgentStatus.RUNNING)
    reg.transition(w.worker_id, AgentStatus.COMPLETED)
    with pytest.raises(InvalidTransitionError):
        reg.transition(w.worker_id, AgentStatus.RUNNING)  # COMPLETED is terminal


def test_any_state_can_fail():
    for start in [
        AgentStatus.IDLE,
        AgentStatus.INITIALIZING,
        AgentStatus.WAITING_FOR_INPUT,
        AgentStatus.RUNNING,
    ]:
        w = AgentWorker(worker_id="test", role="writer", phase="f", status=start)
        w.transition(AgentStatus.FAILED, "error")
        assert w.status == AgentStatus.FAILED


def test_any_non_terminal_state_can_cancel():
    """Sprint 2.4: CANCELLED is reachable from any non-terminal state."""
    for start in [
        AgentStatus.IDLE,
        AgentStatus.INITIALIZING,
        AgentStatus.WAITING_FOR_INPUT,
        AgentStatus.RUNNING,
    ]:
        w = AgentWorker(worker_id="test", role="writer", phase="f", status=start)
        w.transition(AgentStatus.CANCELLED, "cancelled by user")
        assert w.status == AgentStatus.CANCELLED
        assert w.is_terminal


def test_cancelled_is_terminal():
    """Cannot transition out of CANCELLED."""
    w = AgentWorker(worker_id="t", role="r", phase="p", status=AgentStatus.CANCELLED)
    with pytest.raises(InvalidTransitionError):
        w.transition(AgentStatus.RUNNING)


def test_failure_records_error():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    reg.transition(w.worker_id, AgentStatus.FAILED, "LLM timeout")
    assert w.last_error is not None
    assert "LLM timeout" in w.last_error["message"]


def test_worker_rejects_invalid_shape():
    with pytest.raises(ValueError, match="worker_id"):
        AgentWorker(worker_id="", role="writer", phase="f")
    with pytest.raises(ValueError, match="valid AgentStatus"):
        AgentWorker(worker_id="t", role="writer", phase="f", status=cast(Any, "bad"))
    with pytest.raises(TypeError, match="events"):
        AgentWorker(worker_id="t", role="writer", phase="f", events=cast(Any, ()))
    with pytest.raises(ValueError, match="retry_count"):
        AgentWorker(worker_id="t", role="writer", phase="f", retry_count=-1)


def test_worker_coerces_status_values():
    w = AgentWorker(worker_id="t", role="writer", phase="f", status="running")
    assert w.status == AgentStatus.RUNNING


def test_worker_to_dict_does_not_expose_last_error_mutably():
    w = AgentWorker(worker_id="t", role="writer", phase="f")
    w.transition(AgentStatus.FAILED, "boom")
    d = w.to_dict()
    assert d["last_error"]["message"] == "boom"
    d["last_error"]["message"] = "changed"
    assert w.last_error is not None
    assert w.last_error["message"] == "boom"


def test_worker_copies_constructor_mutables():
    event = AgentWorkerEvent(
        seq=1,
        kind="initializing",
        status=AgentStatus.INITIALIZING,
        detail="boot",
        timestamp="2026-01-01T00:00:00+00:00",
    )
    events = [event]
    last_error: dict[str, Any] = {
        "message": "boom",
        "meta": {"attempts": [1]},
    }

    w = AgentWorker(
        worker_id="t",
        role="writer",
        phase="f",
        events=events,
        last_error=last_error,
    )

    events.clear()
    meta = cast(dict[str, list[int]], last_error["meta"])
    meta["attempts"].append(2)

    assert len(w.events) == 1
    assert w.last_error is not None
    assert w.last_error["meta"] == {"attempts": [1]}


def test_worker_to_dict_deep_copies_last_error():
    w = AgentWorker(
        worker_id="t",
        role="writer",
        phase="f",
        last_error={"message": "boom", "meta": {"attempts": [1]}},
    )

    d = w.to_dict()
    d["last_error"]["meta"]["attempts"].append(2)

    assert w.last_error is not None
    assert w.last_error["meta"] == {"attempts": [1]}


def test_list_active_excludes_terminal():
    reg = AgentRegistry()
    w1 = reg.create("writer", "draft")
    w2 = reg.create("reviewer", "draft")
    reg.transition(w1.worker_id, AgentStatus.INITIALIZING)
    reg.transition(w1.worker_id, AgentStatus.RUNNING)
    reg.transition(w2.worker_id, AgentStatus.FAILED, "error")
    active = reg.list_active()
    assert len(active) == 1
    assert active[0].role == "writer"


def test_registry_thread_safe():
    reg = AgentRegistry()
    errors = []

    def create_worker(role: str):
        try:
            w = reg.create(role, "draft")
            reg.transition(w.worker_id, AgentStatus.INITIALIZING)
            reg.transition(w.worker_id, AgentStatus.RUNNING)
            reg.transition(w.worker_id, AgentStatus.COMPLETED)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=create_worker, args=(f"R{i}",)) for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0


def test_get_by_role_phase():
    reg = AgentRegistry()
    reg.create("writer", "draft")
    w2 = reg.create("writer", "review")
    found = reg.get_by_role_phase("writer", "review")
    assert found is not None
    assert found.worker_id == w2.worker_id


def test_registry_lookup_rejects_invalid_keys():
    reg = AgentRegistry()
    with pytest.raises(ValueError, match="worker_id"):
        reg.get("")
    with pytest.raises(ValueError, match="role"):
        reg.get_by_role_phase("", "draft")
    with pytest.raises(TypeError, match="project_id"):
        reg.get_by_project(cast(Any, True))
    with pytest.raises(ValueError, match="project_id"):
        reg.get_by_project(-1)


def test_worker_to_dict():
    reg = AgentRegistry()
    w = reg.create("writer", "draft", project_id=42)
    reg.transition(w.worker_id, AgentStatus.INITIALIZING, "test")
    d = w.to_dict()
    assert d["role"] == "writer"
    assert d["project_id"] == 42
    assert len(d["events"]) == 1


def test_is_terminal():
    w = AgentWorker(
        worker_id="t", role="writer", phase="f", status=AgentStatus.COMPLETED
    )
    assert w.is_terminal is True
    w2 = AgentWorker(
        worker_id="t2", role="writer", phase="f", status=AgentStatus.RUNNING
    )
    assert w2.is_terminal is False
