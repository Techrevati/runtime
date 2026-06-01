"""Tests for the agent-level span nesting in ``OpenTelemetrySink``.

The sink keeps a parent span open between ``AGENT_STARTED`` and
``AGENT_COMPLETED`` / ``AGENT_FAILED`` and emits every other event as
a child of that parent.
"""

from __future__ import annotations

import pytest

# All tests in this module require the optional OTel dependency.
otel_sdk = pytest.importorskip("opentelemetry.sdk.trace")
in_memory_exporter = pytest.importorskip(
    "opentelemetry.sdk.trace.export.in_memory_span_exporter"
)

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode  # noqa: E402

from techrevati.runtime import (  # noqa: E402
    AgentEvent,
    AgentFailureClass,
)
from techrevati.runtime.otel import OpenTelemetrySink  # noqa: E402


def _build_sink_and_exporter() -> tuple[OpenTelemetrySink, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    return OpenTelemetrySink(tracer=tracer), exporter


def test_agent_started_to_completed_produces_one_parent_with_children() -> None:
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(AgentEvent.recovery_attempted("writer", "draft", detail="llm_timeout"))
    sink.emit(AgentEvent.completed("writer", "draft"))

    spans = exporter.get_finished_spans()
    # 1 recovery child + 1 parent (closed last).
    assert len(spans) == 2

    by_name = {s.name: s for s in spans}
    # Parent is the "create_agent writer" / "invoke_agent writer" depending on
    # operation; the open-on-AGENT_STARTED rule uses create_agent.
    parent_names = [n for n in by_name if "writer" in n]
    assert parent_names, f"missing agent span in {list(by_name)}"
    parent = next(s for s in spans if s.kind == SpanKind.INTERNAL and s.parent is None)
    children = [s for s in spans if s.parent is not None]
    assert len(children) == 1
    assert children[0].parent is not None
    assert children[0].parent.span_id == parent.context.span_id


def test_failed_completion_sets_error_status_on_parent() -> None:
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(
        AgentEvent.failed(
            "writer",
            "draft",
            AgentFailureClass.LLM_TIMEOUT,
            detail="model timed out",
        )
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    parent = spans[0]
    assert parent.status.status_code.name == "ERROR"
    assert parent.attributes is not None
    assert parent.attributes.get("error.type") == "llm_timeout"
    assert parent.attributes.get("techrevati.failure_class") == "llm_timeout"


def test_cancelled_completion_does_not_set_error_status_on_parent() -> None:
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(
        AgentEvent.failed(
            "writer",
            "draft",
            AgentFailureClass.CANCELLED,
            detail="async session cancelled",
        )
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    parent = spans[0]
    assert parent.status.status_code != StatusCode.ERROR
    assert parent.attributes is not None
    assert parent.attributes.get("techrevati.failure_class") == "cancelled"
    assert "error.type" not in parent.attributes


def test_tool_call_opens_child_span_under_agent_parent() -> None:
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(AgentEvent.tool_called("writer", "draft", "lookup"))
    sink.emit(AgentEvent.tool_completed("writer", "draft", "lookup"))
    sink.emit(AgentEvent.completed("writer", "draft"))

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    parent = next(span for span in spans if span.parent is None)
    tool = next(span for span in spans if span.parent is not None)
    assert tool.parent is not None
    assert tool.parent.span_id == parent.context.span_id
    assert tool.name == "execute_tool lookup"
    assert tool.attributes is not None
    assert tool.attributes.get("gen_ai.operation.name") == "execute_tool"
    assert tool.attributes.get("techrevati.data.tool") == "lookup"


def test_tool_failure_closes_tool_span_without_closing_agent_parent() -> None:
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(AgentEvent.tool_called("writer", "draft", "lookup"))
    sink.emit(
        AgentEvent.failed(
            "writer",
            "draft",
            AgentFailureClass.TOOL_ERROR,
            detail="tool execution failed: lookup",
        ).with_data({"tool": "lookup"})
    )
    sink.emit(AgentEvent.completed("writer", "draft"))

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    parent = next(span for span in spans if span.parent is None)
    tool = next(span for span in spans if span.parent is not None)
    assert parent.status.status_code != StatusCode.ERROR
    assert parent.attributes is not None
    assert "error.type" not in parent.attributes
    assert tool.status.status_code == StatusCode.ERROR
    assert tool.attributes is not None
    assert tool.attributes.get("error.type") == "tool_error"
    assert tool.attributes.get("techrevati.data.tool") == "lookup"


def test_concurrent_same_tool_calls_each_get_their_own_span() -> None:
    # Regression: two in-flight calls to the same tool used to collide on one
    # (role, phase, tool) key, so the second tool_called force-closed the first
    # span as "tool_span_interrupted". They must now each get their own span.
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(AgentEvent.tool_called("writer", "draft", "lookup"))
    sink.emit(AgentEvent.tool_called("writer", "draft", "lookup"))  # concurrent
    sink.emit(AgentEvent.tool_completed("writer", "draft", "lookup"))
    sink.emit(AgentEvent.tool_completed("writer", "draft", "lookup"))
    sink.emit(AgentEvent.completed("writer", "draft"))

    spans = exporter.get_finished_spans()
    parent = next(s for s in spans if s.parent is None)
    tool_spans = [s for s in spans if s.parent is not None]

    assert len(tool_spans) == 2  # two distinct tool spans, not one
    for tool in tool_spans:
        assert tool.parent is not None
        assert tool.parent.span_id == parent.context.span_id  # sibling children
        assert tool.status.status_code != StatusCode.ERROR
        assert tool.attributes is not None
        assert tool.attributes.get("error.type") != "tool_span_interrupted"


def test_parent_close_ends_all_in_flight_same_tool_spans() -> None:
    # If the agent parent closes while two concurrent calls to the same tool are
    # still open, BOTH must be force-closed as interrupted (not just one).
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(AgentEvent.tool_called("writer", "draft", "lookup"))
    sink.emit(AgentEvent.tool_called("writer", "draft", "lookup"))
    sink.emit(AgentEvent.completed("writer", "draft"))  # parent closes first

    spans = exporter.get_finished_spans()
    tool_spans = [s for s in spans if s.parent is not None]
    assert len(tool_spans) == 2
    for tool in tool_spans:
        assert tool.status.status_code == StatusCode.ERROR
        assert tool.attributes is not None
        assert tool.attributes.get("error.type") == "tool_span_interrupted"


def test_non_terminal_failed_event_with_data_does_not_close_parent() -> None:
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(
        AgentEvent.failed(
            "writer",
            "draft",
            AgentFailureClass.RATE_LIMIT,
            detail="budget exceeded",
        ).with_data({"budget_usd": 1.0})
    )
    sink.emit(AgentEvent.completed("writer", "draft"))

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    parent = next(span for span in spans if span.parent is None)
    budget_event = next(span for span in spans if span.parent is not None)
    assert parent.status.status_code != StatusCode.ERROR
    assert budget_event.status.status_code == StatusCode.ERROR
    assert budget_event.attributes is not None
    assert budget_event.attributes.get("error.type") == "rate_limit"


def test_double_started_closes_previous_parent() -> None:
    """If AGENT_STARTED arrives twice for the same key, the older parent
    is closed first so we don't leak open spans."""
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(AgentEvent.completed("writer", "draft"))

    # Two parents end: the orphaned first one + the proper second one.
    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    # Both should be roots (no parent).
    assert all(s.parent is None for s in spans)


def test_leaf_event_without_parent_emits_one_shot_root() -> None:
    """Backward compat: a leaf event with no active parent still
    surfaces as a stand-alone span instead of being dropped."""
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.recovery_attempted("writer", "draft", detail="x"))

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].parent is None


def test_close_without_open_falls_through_to_one_shot() -> None:
    """AGENT_COMPLETED with no matching open parent is still observable."""
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.completed("writer", "draft", detail="late completion"))

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].parent is None


def test_phase_started_pairs_with_phase_completed() -> None:
    """``phase.*`` events follow the same open/close rule as agent.*."""
    sink, exporter = _build_sink_and_exporter()

    sink.emit(AgentEvent.phase_started("draft"))
    sink.emit(AgentEvent.gate_passed("draft", detail="quality ok"))
    sink.emit(
        AgentEvent(
            event=AgentEvent.phase_started("draft").event.__class__.PHASE_COMPLETED,
            status=AgentEvent.phase_started("draft").status.__class__.COMPLETED,
            phase="draft",
        )
    )

    spans = exporter.get_finished_spans()
    parent_count = sum(1 for s in spans if s.parent is None)
    child_count = sum(1 for s in spans if s.parent is not None)
    assert parent_count == 1
    assert child_count == 1
