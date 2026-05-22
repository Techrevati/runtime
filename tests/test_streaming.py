"""Tests for streaming.StreamEvent and AsyncOrchestrationSession.arun_turn_stream."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

import pytest

from techrevati.runtime import (
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
async def test_arun_turn_stream_upstream_exception_yields_error_and_final() -> None:
    async def failing() -> AsyncIterator[str]:
        yield "first"
        raise RuntimeError("boom")

    sess = AgentSession(role="writer", phase="draft")
    async with sess.asession() as session:
        received: list[StreamEvent] = []
        with pytest.raises(RuntimeError, match="boom"):
            async for ev in session.arun_turn_stream(failing, model="m"):
                received.append(ev)
    # We saw text_delta → error → final (then the raise propagated)
    types = [e.type for e in received]
    assert types == ["text_delta", "error", "final"]
    assert received[-1].payload["status"] == "failed"
    assert received[-2].payload["error_type"] == "RuntimeError"


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
            lambda: _chunks("a", "b", "c"), model="claude"
        ):
            pass
    assert calls == ["claude"]


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
