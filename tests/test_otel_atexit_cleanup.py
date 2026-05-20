"""OpenTelemetrySink — orphan-parent-span cleanup on abrupt termination."""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry")

from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode  # noqa: E402

from techrevati.runtime.agent_events import (  # noqa: E402
    AgentEvent,
    AgentEventName,
    AgentEventStatus,
)
from techrevati.runtime.otel import (  # noqa: E402
    OpenTelemetrySink,
    _flush_orphan_parent_spans_at_exit,
)


def _fresh_tracer():
    """Build a fresh tracer + exporter pair so tests don't share state."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _started_event() -> AgentEvent:
    return AgentEvent(
        event=AgentEventName.AGENT_STARTED,
        status=AgentEventStatus.RUNNING,
        role="writer",
        phase="draft",
    )


def test_orphan_parent_span_flushed_with_error_status_on_atexit():
    tracer, exporter = _fresh_tracer()
    sink = OpenTelemetrySink(tracer=tracer)

    sink.emit(_started_event())  # opens parent span
    assert len(sink._active_spans) == 1

    # Simulate process exit without AGENT_COMPLETED/FAILED/PHASE_COMPLETED.
    _flush_orphan_parent_spans_at_exit()

    assert sink._active_spans == {}
    finished = exporter.get_finished_spans()
    assert len(finished) == 1
    orphan = finished[0]
    assert orphan.status.status_code == StatusCode.ERROR
    assert orphan.attributes.get("error.type") == "abrupt_termination"


def test_atexit_flush_is_idempotent():
    tracer, exporter = _fresh_tracer()
    sink = OpenTelemetrySink(tracer=tracer)
    sink.emit(_started_event())

    _flush_orphan_parent_spans_at_exit()
    _flush_orphan_parent_spans_at_exit()

    # Only one parent ever opened, only one ever ended
    finished = exporter.get_finished_spans()
    assert len(finished) == 1


def test_atexit_flush_does_not_double_close_normal_lifecycle():
    """If AGENT_COMPLETED already closed the parent, atexit has nothing to do."""
    tracer, exporter = _fresh_tracer()
    sink = OpenTelemetrySink(tracer=tracer)
    sink.emit(_started_event())
    sink.emit(
        AgentEvent(
            event=AgentEventName.AGENT_COMPLETED,
            status=AgentEventStatus.COMPLETED,
            role="writer",
            phase="draft",
        )
    )
    assert sink._active_spans == {}

    _flush_orphan_parent_spans_at_exit()

    finished = exporter.get_finished_spans()
    # One parent span; clean OK status (not ERROR)
    assert len(finished) == 1
    assert finished[0].status.status_code != StatusCode.ERROR


def test_dropped_sink_does_not_pin_memory_via_atexit():
    """The WeakSet registration must allow a dropped sink to be GC'd."""
    import gc
    import weakref

    tracer, _ = _fresh_tracer()
    sink = OpenTelemetrySink(tracer=tracer)
    ref = weakref.ref(sink)
    del sink
    gc.collect()
    assert ref() is None  # sink was collected; atexit reg didn't pin it
