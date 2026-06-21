"""AgentSession ``audit_log`` wiring — Article 12 record-keeping end to end."""

from __future__ import annotations

import pytest

from techrevati.runtime import AgentSession, RingBufferEventSink
from techrevati.runtime.compliance import AuditLogSink, InMemoryAuditBackend


def test_audit_log_captures_session_lifecycle_and_tool_calls() -> None:
    audit = AuditLogSink(InMemoryAuditBackend())
    user_sink = RingBufferEventSink()
    factory = AgentSession(
        role="planner", phase="design", event_sink=user_sink, audit_log=audit
    )

    with factory.session() as session:
        assert session.run_tool("search", lambda: "result") == "result"

    event_types = [r.event_type for r in audit.records()]
    assert "agent.started" in event_types
    assert "agent.tool_called" in event_types
    assert "agent.tool_completed" in event_types
    # Tamper-evident chain stays intact.
    assert audit.verify_chain().valid

    # Fan-out preserves the caller's own sink unchanged.
    user_events = [e.event.value for e in user_sink.events]
    assert "agent.started" in user_events
    assert "agent.tool_called" in user_events


def test_session_without_audit_log_leaves_sink_untouched() -> None:
    user_sink = RingBufferEventSink()
    factory = AgentSession(role="planner", phase="design", event_sink=user_sink)
    with factory.session() as session:
        session.run_tool("noop", lambda: None)
    assert any(e.event.value == "agent.started" for e in user_sink.events)


@pytest.mark.asyncio
async def test_audit_log_captures_async_session() -> None:
    audit = AuditLogSink(InMemoryAuditBackend())
    factory = AgentSession(role="builder", phase="impl", audit_log=audit)
    async with factory.asession() as session:
        await session.arun_tool("fetch", lambda: _async_value("ok"))
    assert audit.verify_chain().valid
    assert any(r.event_type == "agent.tool_completed" for r in audit.records())


async def _async_value(value: str) -> str:
    return value
