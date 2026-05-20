"""Tests for techrevati.runtime.handoffs (Sprint 3.2 + 3.3)."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    AgentSession,
    AgentStatus,
    Handoff,
)


def test_handoff_to_creates_target_worker_and_finalizes_source():
    orch = AgentSession(role="writer", phase="draft", project_id=42)
    with orch.session() as session:
        handoff = session.handoff_to(
            "editor", reason="review please", context={"draft_id": 7}
        )

    assert isinstance(handoff, Handoff)
    assert handoff.source_role == "writer"
    assert handoff.target_role == "editor"
    assert handoff.reason == "review please"
    assert handoff.context == {"draft_id": 7}
    assert handoff.project_id == 42
    assert handoff.target_worker_id  # non-empty

    # Source worker is COMPLETED (handoff finalizes it).
    assert session.worker.status == AgentStatus.COMPLETED

    # Target worker exists in the same registry, INITIALIZING.
    target = orch.registry.get(handoff.target_worker_id)
    assert target is not None
    assert target.role == "editor"
    assert target.status == AgentStatus.INITIALIZING
    assert target.project_id == 42


def test_handoff_event_is_recorded_on_source_session():
    orch = AgentSession(role="writer", phase="draft")
    with orch.session() as session:
        session.handoff_to("editor", reason="review")
    completed_with_handoff = [
        e
        for e in session.events
        if e.event.value == "agent.completed" and "handoff" in (e.detail or "")
    ]
    assert len(completed_with_handoff) >= 1


def test_handoff_serialization_roundtrip():
    h = Handoff(
        source_role="a",
        target_role="b",
        phase="p",
        reason="r",
        context={"k": "v"},
        project_id=1,
        target_worker_id="abc",
    )
    d = h.to_dict()
    assert d["source_role"] == "a"
    assert d["target_role"] == "b"
    assert d["context"] == {"k": "v"}
    assert d["project_id"] == 1


def test_handoff_default_context_is_empty_dict():
    orch = AgentSession(role="writer", phase="draft")
    with orch.session() as session:
        handoff = session.handoff_to("editor", reason="r")
    assert handoff.context == {}


@pytest.mark.asyncio
async def test_async_handoff_to_finalizes_source():
    orch = AgentSession(role="writer", phase="draft", project_id=99)
    async with orch.asession() as session:
        handoff = session.handoff_to("editor", reason="async review")
    assert handoff.target_role == "editor"
    assert session.worker.status == AgentStatus.COMPLETED
    target = orch.registry.get(handoff.target_worker_id)
    assert target is not None
    assert target.role == "editor"
    assert target.project_id == 99
