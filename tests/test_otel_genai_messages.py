"""GenAI semconv message-body span events (opt-in, content-gated)."""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk.trace")
pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from techrevati.runtime import AgentEvent  # noqa: E402
from techrevati.runtime.otel import OpenTelemetrySink  # noqa: E402


def _sink(*, include_event_detail: bool):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    sink = OpenTelemetrySink(tracer=tracer, include_event_detail=include_event_detail)
    return sink, exporter


def _event_names(exporter: InMemorySpanExporter) -> list[str]:
    return [e.name for s in exporter.get_finished_spans() for e in s.events]


def _emit_with_messages(sink) -> None:
    sink.emit(AgentEvent.started("writer", "draft"))
    sink.emit(
        AgentEvent.ready(
            "writer",
            "draft",
            data={
                "gen_ai.input.messages": [{"role": "user", "content": "hi"}],
                "gen_ai.output.messages": [{"role": "assistant", "content": "yo"}],
            },
        )
    )
    sink.emit(AgentEvent.completed("writer", "draft"))


def test_messages_emitted_as_span_events_when_detail_enabled() -> None:
    sink, exporter = _sink(include_event_detail=True)
    _emit_with_messages(sink)
    names = _event_names(exporter)
    assert "gen_ai.input.messages" in names
    assert "gen_ai.output.messages" in names


def test_messages_suppressed_when_detail_disabled() -> None:
    sink, exporter = _sink(include_event_detail=False)
    _emit_with_messages(sink)
    names = _event_names(exporter)
    assert "gen_ai.input.messages" not in names
    assert "gen_ai.output.messages" not in names


def test_message_keys_not_flattened_to_attributes() -> None:
    sink, exporter = _sink(include_event_detail=True)
    _emit_with_messages(sink)
    for span in exporter.get_finished_spans():
        assert "techrevati.data.gen_ai.input.messages" not in span.attributes
