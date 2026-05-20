"""Tests for techrevati.runtime.otel (Sprint 4.3-4.6).

Uses the OpenTelemetry SDK in-memory exporter so we can assert on the
exact attributes our sink emits.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry")

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import (  # noqa: E402
    InMemoryMetricReader,
)
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

from techrevati.runtime import (  # noqa: E402
    ModelPricing,
    Orchestrator,
    UsageSnapshot,
    register_pricing,
)
from techrevati.runtime.otel import (  # noqa: E402
    OpenTelemetrySink,
    OpenTelemetryUsageSink,
)


@pytest.fixture
def in_memory_tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    return tracer, exporter


@pytest.fixture
def in_memory_meter():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    return meter, reader


@pytest.fixture(autouse=True)
def _register_pricing():
    register_pricing("test-model", ModelPricing(3.0, 15.0))


def test_otel_event_sink_emits_invoke_agent_span(in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = OpenTelemetrySink(tracer=tracer, agent_id="abc")
    orch = Orchestrator(role="writer", phase="draft", event_sink=sink)

    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=100),
        )

    spans = exporter.get_finished_spans()
    assert len(spans) >= 1
    # The completion event of run_turn should appear as an invoke_agent span.
    op_names = {s.attributes.get("gen_ai.operation.name") for s in spans}
    assert "invoke_agent" in op_names
    # agent.id and provider.name should be set on every span.
    for s in spans:
        assert s.attributes.get("gen_ai.provider.name") == "techrevati"
        assert s.attributes.get("gen_ai.agent.id") == "abc"


def test_otel_event_sink_sets_error_type_on_failure(in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = OpenTelemetrySink(tracer=tracer)
    orch = Orchestrator(role="writer", phase="draft", event_sink=sink)

    with pytest.raises(RuntimeError):
        with orch.session() as session:
            session.run_turn(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    spans = exporter.get_finished_spans()
    error_spans = [s for s in spans if s.attributes.get("error.type")]
    assert error_spans, "expected at least one span with error.type"


def test_otel_usage_sink_records_token_and_cost_metrics(in_memory_meter):
    meter, reader = in_memory_meter
    usage_sink = OpenTelemetryUsageSink(meter=meter)
    orch = Orchestrator(role="writer", phase="draft", usage_sink=usage_sink)

    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=1000, output_tokens=500),
        )

    metrics_data = reader.get_metrics_data()
    metric_names: set[str] = set()
    for resource_metric in metrics_data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                metric_names.add(metric.name)

    assert "gen_ai.client.token.usage" in metric_names
    assert "techrevati.cost.usd" in metric_names


def test_otel_sink_raises_clear_error_when_otel_missing(monkeypatch):
    """If opentelemetry isn't installed, instantiating the sink should
    raise an ImportError with a clear hint."""
    import techrevati.runtime.otel as otel_mod

    monkeypatch.setattr(otel_mod, "_OTEL_AVAILABLE", False)
    monkeypatch.setattr(otel_mod, "_OTEL_IMPORT_ERROR", ImportError("simulated"))
    with pytest.raises(ImportError, match=r"techrevati-runtime\[otel\]"):
        OpenTelemetrySink()
