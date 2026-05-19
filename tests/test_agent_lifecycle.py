"""Tests for agent_patterns.agent_lifecycle"""

import threading
import pytest
from techrevati.runtime.agent_lifecycle import (
    AgentStatus, AgentWorker, AgentRegistry, InvalidTransitionError,
)


def test_create_worker_starts_idle():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    assert w.status == AgentStatus.IDLE
    assert w.role == "writer"
    assert w.phase == "draft"
    assert len(w.events) == 0


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


def test_terminal_state_blocks_transitions():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    reg.transition(w.worker_id, AgentStatus.INITIALIZING)
    reg.transition(w.worker_id, AgentStatus.RUNNING)
    reg.transition(w.worker_id, AgentStatus.COMPLETED)
    with pytest.raises(InvalidTransitionError):
        reg.transition(w.worker_id, AgentStatus.RUNNING)  # COMPLETED is terminal


def test_any_state_can_fail():
    for start in [AgentStatus.IDLE, AgentStatus.INITIALIZING,
                  AgentStatus.WAITING_FOR_INPUT, AgentStatus.RUNNING]:
        w = AgentWorker(worker_id="test", role="writer", phase="f", status=start)
        w.transition(AgentStatus.FAILED, "error")
        assert w.status == AgentStatus.FAILED


def test_failure_records_error():
    reg = AgentRegistry()
    w = reg.create("writer", "draft")
    reg.transition(w.worker_id, AgentStatus.FAILED, "LLM timeout")
    assert w.last_error is not None
    assert "LLM timeout" in w.last_error["message"]


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

    threads = [threading.Thread(target=create_worker, args=(f"R{i}",)) for i in range(10)]
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


def test_worker_to_dict():
    reg = AgentRegistry()
    w = reg.create("writer", "draft", project_id=42)
    reg.transition(w.worker_id, AgentStatus.INITIALIZING, "test")
    d = w.to_dict()
    assert d["role"] == "writer"
    assert d["project_id"] == 42
    assert len(d["events"]) == 1


def test_is_terminal():
    w = AgentWorker(worker_id="t", role="writer", phase="f", status=AgentStatus.COMPLETED)
    assert w.is_terminal is True
    w2 = AgentWorker(worker_id="t2", role="writer", phase="f", status=AgentStatus.RUNNING)
    assert w2.is_terminal is False
