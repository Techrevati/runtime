"""Tests for techrevati.runtime.handoffs (Sprint 3.2 + 3.3)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from techrevati.runtime import (
    AgentSession,
    AgentStatus,
    Handoff,
    HookContext,
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


def test_handoff_rejects_invalid_shape():
    with pytest.raises(ValueError, match="source_role"):
        Handoff(source_role="", target_role="b", phase="p", reason="r")
    with pytest.raises(ValueError, match="target_role"):
        Handoff(source_role="a", target_role=" ", phase="p", reason="r")
    with pytest.raises(ValueError, match="reason"):
        Handoff(source_role="a", target_role="b", phase="p", reason="")
    with pytest.raises(TypeError, match="context"):
        Handoff(source_role="a", target_role="b", phase="p", reason="r", context=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="context keys"):
        Handoff(
            source_role="a",
            target_role="b",
            phase="p",
            reason="r",
            context=cast(Any, {1: "bad"}),
        )
    with pytest.raises(ValueError, match="project_id"):
        Handoff(source_role="a", target_role="b", phase="p", reason="r", project_id=-1)
    with pytest.raises(TypeError, match="target_worker_id"):
        Handoff(
            source_role="a",
            target_role="b",
            phase="p",
            reason="r",
            target_worker_id=cast(Any, None),
        )
    with pytest.raises(ValueError, match="target_worker_id"):
        Handoff(
            source_role="a",
            target_role="b",
            phase="p",
            reason="r",
            target_worker_id="   ",
        )


def test_handoff_context_is_copied_on_create_and_serialize():
    context: dict[str, Any] = {"k": {"values": [1]}}
    h = Handoff(
        source_role="a", target_role="b", phase="p", reason="r", context=context
    )
    context["later"] = True
    context["k"]["values"].append(2)
    assert "later" not in h.context
    assert h.context == {"k": {"values": [1]}}

    d = h.to_dict()
    d["context"]["extra"] = True
    d["context"]["k"]["values"].append(3)
    assert "extra" not in h.context
    assert h.context == {"k": {"values": [1]}}


def test_handoff_target_worker_id_is_stripped():
    h = Handoff(
        source_role="a",
        target_role="b",
        phase="p",
        reason="r",
        target_worker_id=" worker-1 ",
    )
    assert h.target_worker_id == "worker-1"


def test_handoff_default_context_is_empty_dict():
    orch = AgentSession(role="writer", phase="draft")
    with orch.session() as session:
        handoff = session.handoff_to("editor", reason="r")
    assert handoff.context == {}


def test_handoff_to_runs_before_handoff_hooks():
    calls: list[tuple[str, str, str, str, dict[str, Any]]] = []

    class RouteHook:
        name = "route"

        def before_handoff(self, ctx: HookContext) -> None:
            calls.append(
                (
                    ctx.role,
                    ctx.phase,
                    ctx.extra["target_role"],
                    ctx.extra["reason"],
                    ctx.extra["context"],
                )
            )
            ctx.extra["target_role"] = "senior-editor"
            ctx.extra["reason"] = "review approved"
            ctx.extra["context"]["priority"] = "high"

    orch = AgentSession(role="writer", phase="draft", hooks=[RouteHook()])
    with orch.session() as session:
        handoff = session.handoff_to(
            "editor",
            reason="review",
            context={"draft_id": 7},
        )

    assert calls == [
        ("writer", "draft", "editor", "review", {"draft_id": 7, "priority": "high"})
    ]
    assert handoff.target_role == "senior-editor"
    assert handoff.reason == "review approved"
    assert handoff.context == {"draft_id": 7, "priority": "high"}
    target = orch.registry.get(handoff.target_worker_id)
    assert target is not None
    assert target.role == "senior-editor"


def test_handoff_hook_error_blocks_target_registration():
    class BlockingHook:
        name = "blocking"

        def before_handoff(self, ctx: HookContext) -> None:
            raise RuntimeError("handoff blocked")

    orch = AgentSession(role="writer", phase="draft", hooks=[BlockingHook()])
    before_count = 0
    with pytest.raises(RuntimeError, match="handoff blocked"):
        with orch.session() as session:
            before_count = len(orch.registry._workers)  # noqa: SLF001
            session.handoff_to("editor", reason="review")

    assert len(orch.registry._workers) == before_count  # noqa: SLF001


def test_handoff_to_validates_before_registering_target_worker():
    orch = AgentSession(role="writer", phase="draft")
    before_count = 0
    with pytest.raises(ValueError, match="reason"):
        with orch.session() as session:
            before_count = len(orch.registry._workers)  # noqa: SLF001
            session.handoff_to("editor", reason="")

    assert len(orch.registry._workers) == before_count  # noqa: SLF001


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


@pytest.mark.asyncio
async def test_async_handoff_to_awaits_async_hooks():
    calls: list[str] = []

    class AsyncRouteHook:
        name = "async_route"

        async def abefore_handoff(self, ctx: HookContext) -> None:
            calls.append(ctx.extra["target_role"])
            ctx.extra["target_role"] = "async-editor"
            ctx.extra["context"]["async"] = True

    orch = AgentSession(role="writer", phase="draft", hooks=[AsyncRouteHook()])
    async with orch.asession() as session:
        handoff = await session.ahandoff_to(
            "editor",
            reason="async review",
            context={"draft_id": 8},
        )

    assert calls == ["editor"]
    assert handoff.target_role == "async-editor"
    assert handoff.context == {"draft_id": 8, "async": True}
    target = orch.registry.get(handoff.target_worker_id)
    assert target is not None
    assert target.role == "async-editor"
