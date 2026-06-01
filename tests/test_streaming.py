"""Tests for streaming.StreamEvent and AsyncOrchestrationSession.arun_turn_stream."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any, cast

import pytest

from techrevati.runtime import (
    AgentFailureClass,
    AgentSession,
    HookContext,
    StreamEvent,
    TurnTimeoutError,
    UsageSnapshot,
)

# ---------------------------------------------------------------------------
# StreamEvent constructors + serialization
# ---------------------------------------------------------------------------


def test_stream_event_text_constructor() -> None:
    ev = StreamEvent.text("hello")
    assert ev.type == "text_delta"
    assert ev.payload == {"delta": "hello"}
    assert ev.emitted_at  # iso timestamp present


def test_stream_event_tool_call_default_args() -> None:
    ev = StreamEvent.tool_call("search")
    assert ev.type == "tool_call"
    assert ev.payload == {"tool": "search", "args": {}}


def test_stream_event_tool_result() -> None:
    ev = StreamEvent.tool_result("search", {"hits": 3})
    assert ev.payload == {"tool": "search", "result": {"hits": 3}}


def test_stream_event_handoff() -> None:
    ev = StreamEvent.handoff("reviewer", "needs human review")
    assert ev.type == "handoff"
    assert ev.payload == {"target_role": "reviewer", "reason": "needs human review"}


def test_stream_event_final_minimal() -> None:
    ev = StreamEvent.final("completed")
    assert ev.payload == {"status": "completed"}


def test_stream_event_final_with_detail_and_usage() -> None:
    ev = StreamEvent.final("failed", detail="timeout", usage={"input_tokens": 10})
    assert ev.payload == {
        "status": "failed",
        "detail": "timeout",
        "usage": {"input_tokens": 10},
    }


def test_stream_event_error() -> None:
    ev = StreamEvent.error("ValueError", "bad input")
    assert ev.payload == {"error_type": "ValueError", "message": "bad input"}


def test_stream_event_to_dict_roundtrip_json() -> None:
    ev = StreamEvent.text("hi")
    payload = json.loads(ev.to_json())
    assert payload["type"] == "text_delta"
    assert payload["payload"] == {"delta": "hi"}


def test_stream_event_copies_payload_inputs_and_outputs() -> None:
    payload: dict[str, Any] = {"tool": "search", "args": {"q": "x"}}
    ev = StreamEvent(type="tool_call", payload=payload)
    payload["later"] = True
    payload["args"]["q"] = "changed"
    assert "later" not in ev.payload
    assert ev.payload["args"] == {"q": "x"}

    serialized = ev.to_dict()
    serialized["payload"]["extra"] = True
    serialized["payload"]["args"]["q"] = "serialized-change"
    assert "extra" not in ev.payload
    assert ev.payload["args"] == {"q": "x"}


def test_stream_event_constructor_payloads_are_deep_copied() -> None:
    args = {"filters": {"tags": ["a"]}}
    ev = StreamEvent.tool_call("search", args=args)
    args["filters"]["tags"].append("b")

    assert ev.payload["args"] == {"filters": {"tags": ["a"]}}

    usage = {"nested": {"tokens": [1]}}
    final = StreamEvent.final("completed", usage=usage)
    usage["nested"]["tokens"].append(2)

    assert final.payload["usage"] == {"nested": {"tokens": [1]}}


def test_stream_event_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError, match="valid stream event type"):
        StreamEvent(type=cast(Any, "unknown"))
    with pytest.raises(TypeError, match="payload"):
        StreamEvent(type="text_delta", payload=cast(Any, []))
    with pytest.raises(TypeError, match="payload keys"):
        StreamEvent(type="text_delta", payload=cast(Any, {1: "bad"}))
    with pytest.raises(ValueError, match="emitted_at"):
        StreamEvent(type="text_delta", payload={"delta": "x"}, emitted_at="")


def test_stream_event_direct_constructor_validates_payload_contract() -> None:
    with pytest.raises(ValueError, match="text_delta payload requires 'delta'"):
        StreamEvent(type="text_delta")
    with pytest.raises(TypeError, match="payload.delta"):
        StreamEvent(type="text_delta", payload={"delta": 1})
    with pytest.raises(ValueError, match="tool_call payload requires 'args'"):
        StreamEvent(type="tool_call", payload={"tool": "search"})
    with pytest.raises(TypeError, match="payload.args"):
        StreamEvent(type="tool_call", payload={"tool": "search", "args": []})
    with pytest.raises(ValueError, match="tool_result payload requires 'result'"):
        StreamEvent(type="tool_result", payload={"tool": "search"})
    with pytest.raises(ValueError, match="payload.reason"):
        StreamEvent(type="handoff", payload={"target_role": "r", "reason": " "})
    with pytest.raises(ValueError, match="valid stream final status"):
        StreamEvent(type="final", payload={"status": "done"})
    with pytest.raises(TypeError, match="payload.usage"):
        StreamEvent(type="final", payload={"status": "completed", "usage": []})
    with pytest.raises(ValueError, match="payload.message"):
        StreamEvent(type="error", payload={"error_type": "ValueError", "message": ""})


def test_stream_event_direct_constructor_preserves_extra_payload_fields() -> None:
    ev = StreamEvent(
        type="final",
        payload={
            "status": "completed",
            "usage": {"input_tokens": 1},
            "trace_id": "abc",
        },
    )

    assert ev.payload == {
        "status": "completed",
        "usage": {"input_tokens": 1},
        "trace_id": "abc",
    }


def test_stream_event_constructors_reject_invalid_inputs() -> None:
    with pytest.raises(TypeError, match="delta"):
        StreamEvent.text(cast(Any, 1))
    with pytest.raises(ValueError, match="tool"):
        StreamEvent.tool_call("")
    with pytest.raises(TypeError, match="args"):
        StreamEvent.tool_call("search", args=cast(Any, []))
    with pytest.raises(ValueError, match="valid stream final status"):
        StreamEvent.final(cast(Any, "done"))
    with pytest.raises(TypeError, match="usage"):
        StreamEvent.final("completed", usage=cast(Any, []))
    with pytest.raises(ValueError, match="error_type"):
        StreamEvent.error("", "bad")


def test_stream_event_is_frozen() -> None:
    ev = StreamEvent.text("x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.type = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# arun_turn_stream — happy path
# ---------------------------------------------------------------------------


async def _chunks(*items: str) -> AsyncIterator[str]:
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_arun_turn_stream_yields_text_then_final() -> None:
    sess = AgentSession(role="writer", phase="draft")
    async with sess.asession() as session:
        events = [
            ev
            async for ev in session.arun_turn_stream(
                lambda: _chunks("Hel", "lo", " world"),
                model="m",
                usage=UsageSnapshot(input_tokens=5, output_tokens=3),
            )
        ]
    types = [e.type for e in events]
    assert types == ["text_delta", "text_delta", "text_delta", "final"]
    deltas = [e.payload["delta"] for e in events if e.type == "text_delta"]
    assert "".join(deltas) == "Hello world"
    final = events[-1]
    assert final.payload["status"] == "completed"
    assert final.payload["usage"]["input_tokens"] == 5


@pytest.mark.asyncio
async def test_arun_turn_stream_aggregate_passes_through_after_model_hook() -> None:
    captured: dict[str, str] = {}

    class CaptureAfter:
        name = "capture_after"

        def after_model(self, ctx: HookContext, result: str) -> str:
            captured["full"] = result
            return result + "!"

    sess = AgentSession(role="writer", phase="draft", hooks=[CaptureAfter()])
    async with sess.asession() as session:
        events = [
            ev
            async for ev in session.arun_turn_stream(
                lambda: _chunks("Hi ", "there"),
                model="m",
            )
        ]
    assert captured["full"] == "Hi there"
    # final usage snapshot is computed from the post-hook result via the
    # estimator (none passed → default empty UsageSnapshot).
    assert events[-1].payload["status"] == "completed"


# ---------------------------------------------------------------------------
# arun_turn_stream — cancellation semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arun_turn_stream_consumer_break_sets_cancelled_flag() -> None:
    """Consumer wraps with aclosing() so break triggers explicit aclose().

    Without aclosing() (or an explicit aclose() call), Python does NOT
    close an async generator when an ``async for`` loop is broken — the
    generator lingers until garbage collection. ``aclosing`` is the
    documented idiom for getting deterministic cleanup; the runtime
    docs surface this pattern in ``docs/patterns/streaming.md``.
    """
    upstream_closed = asyncio.Event()

    async def closeable_chunks() -> AsyncIterator[str]:
        try:
            for i in range(100):
                yield f"chunk-{i}"
        finally:
            upstream_closed.set()

    sess = AgentSession(role="writer", phase="draft")
    async with sess.asession() as session:
        received: list[StreamEvent] = []
        async with aclosing(
            session.arun_turn_stream(closeable_chunks, model="m")
        ) as agen:
            async for ev in agen:
                received.append(ev)
                if len(received) >= 2:
                    break
        assert session._last_stream_cancelled is True
    # Upstream finally block ran (consumer-break path closed the generator).
    assert upstream_closed.is_set()


@pytest.mark.asyncio
async def test_arun_turn_stream_records_aclose_failure_diagnostic(caplog) -> None:
    class BrokenCloseStream:
        def __init__(self) -> None:
            self._sent = False

        def __aiter__(self) -> BrokenCloseStream:
            return self

        async def __anext__(self) -> str:
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return "done"

        async def aclose(self) -> None:
            raise RuntimeError("socket secret details")

    sess = AgentSession(role="writer", phase="draft", project_id=42)
    async with sess.asession() as session:
        with caplog.at_level(logging.ERROR, logger="techrevati.runtime.orchestrator"):
            received = [
                ev
                async for ev in session.arun_turn_stream(
                    BrokenCloseStream,
                    model="m",
                )
            ]

    assert received[-1].payload["status"] == "completed"
    diagnostics = [
        event
        for event in session.events
        if event.data and event.data.get("component") == "stream_upstream"
    ]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.project_id == 42
    assert diagnostic.failure_class == AgentFailureClass.DEPENDENCY_FAILED
    assert diagnostic.detail == "stream_upstream failed; session continued"
    assert diagnostic.data == {
        "component": "stream_upstream",
        "error_type": "RuntimeError",
    }
    assert "secret details" not in str(diagnostic.to_dict())
    assert any("upstream.aclose() raised" in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
    assert "secret details" not in caplog.text


@pytest.mark.asyncio
async def test_arun_turn_stream_upstream_exception_yields_error_and_final() -> None:
    async def failing() -> AsyncIterator[str]:
        yield "first"
        raise RuntimeError("connection string with sensitive details")

    sess = AgentSession(role="writer", phase="draft")
    async with sess.asession() as session:
        received: list[StreamEvent] = []
        with pytest.raises(RuntimeError, match="sensitive details"):
            async for ev in session.arun_turn_stream(failing, model="m"):
                received.append(ev)
    # We saw text_delta → error → final (then the raise propagated)
    types = [e.type for e in received]
    assert types == ["text_delta", "error", "final"]
    assert received[-1].payload["status"] == "failed"
    assert received[-1].payload["detail"] == "RuntimeError raised"
    assert received[-2].payload["error_type"] == "RuntimeError"
    assert received[-2].payload["message"] == "RuntimeError raised"
    assert "sensitive" not in str([event.to_dict() for event in received])


@pytest.mark.asyncio
async def test_arun_turn_stream_records_failure_when_consumer_breaks_on_final() -> None:
    """Regression: recovery/governance recording must survive a consumer that
    stops iterating the moment it sees the terminal ``final`` event. The old
    code recorded *after* yielding ``final``, so breaking there threw
    GeneratorExit at that yield and silently dropped the failure record."""

    async def failing() -> AsyncIterator[str]:
        yield "first"
        raise RuntimeError("connection string with sensitive details")

    sess = AgentSession(role="writer", phase="draft")
    async with sess.asession() as session:
        received: list[StreamEvent] = []
        async with aclosing(session.arun_turn_stream(failing, model="m")) as agen:
            async for ev in agen:
                received.append(ev)
                if ev.type == "final":
                    break  # break first → the generator's re-raise never runs

    assert received[-1].payload["status"] == "failed"
    # Failure outcome was recorded despite breaking on `final`, and this is a
    # failure path — not the consumer-cancellation path.
    assert len(session.recovery.events) >= 1
    assert session._last_stream_cancelled is False


@pytest.mark.asyncio
async def test_arun_turn_stream_timeout_raises_turn_timeout_error() -> None:
    async def slow() -> AsyncIterator[str]:
        yield "first"
        await asyncio.sleep(5.0)  # never reached before timeout
        yield "second"

    sess = AgentSession(role="writer", phase="draft")
    async with sess.asession() as session:
        received: list[StreamEvent] = []
        with pytest.raises(TurnTimeoutError):
            async for ev in session.arun_turn_stream(slow, model="m", timeout=0.05):
                received.append(ev)
    # Stream emitted the first chunk + timeout error + failed final
    types = [e.type for e in received]
    assert "text_delta" in types
    assert "error" in types
    assert "final" in types
    assert any(event.scenario == "llm_timeout" for event in session.recovery.events)
    assert any(
        event.event.value == "agent.recovery.attempted"
        and event.detail == "llm_timeout: recovered"
        for event in session.events
    )


# ---------------------------------------------------------------------------
# Hook integration with stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arun_turn_stream_before_model_hook_runs_once() -> None:
    """before_model fires once before the stream starts, not per chunk."""

    calls: list[str] = []

    class CountBefore:
        name = "count_before"

        def before_model(self, ctx: HookContext) -> None:
            calls.append(ctx.model)

    sess = AgentSession(role="writer", phase="draft", hooks=[CountBefore()])
    async with sess.asession() as session:
        async for _ in session.arun_turn_stream(
            lambda: _chunks("a", "b", "c"), model="your-model"
        ):
            pass
    assert calls == ["your-model"]


@pytest.mark.asyncio
async def test_arun_turn_stream_with_explicit_hook_context() -> None:
    """Caller-supplied HookContext is threaded through hooks."""

    seen_prompt: list[Any] = []

    class Spy:
        name = "spy"

        def before_model(self, ctx: HookContext) -> None:
            seen_prompt.append(ctx.prompt)

    ctx = HookContext(role="writer", phase="draft", prompt={"text": "user prompt"})
    sess = AgentSession(role="writer", phase="draft", hooks=[Spy()])
    async with sess.asession() as session:
        async for _ in session.arun_turn_stream(
            lambda: _chunks("ok"), model="m", hook_ctx=ctx
        ):
            pass
    assert seen_prompt == [{"text": "user prompt"}]
