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

import techrevati.runtime.otel as otel_mod  # noqa: E402
from techrevati import runtime as runtime_pkg  # noqa: E402
from techrevati.runtime import (  # noqa: E402
    AgentEvent,
    AgentSession,
    ModelPricing,
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


def test_otel_instrumentation_version_matches_package_version():
    assert otel_mod._instrumentation_version() == runtime_pkg.__version__


def test_otel_default_tracer_uses_runtime_instrumentation_version(monkeypatch):
    calls = []
    tracer = object()
    monkeypatch.setattr(otel_mod, "_instrumentation_version", lambda: "9.9.9")
    monkeypatch.setattr(
        otel_mod.trace,
        "get_tracer",
        lambda name, version: calls.append((name, version)) or tracer,
    )

    sink = OpenTelemetrySink()

    assert sink.tracer is tracer
    assert calls == [("techrevati.runtime", "9.9.9")]


def test_otel_default_meter_uses_runtime_instrumentation_version(monkeypatch):
    class FakeMeter:
        def create_histogram(self, **kwargs):
            return object()

        def create_counter(self, **kwargs):
            return object()

    calls = []
    meter = FakeMeter()
    monkeypatch.setattr(otel_mod, "_instrumentation_version", lambda: "9.9.9")
    monkeypatch.setattr(
        otel_mod.metrics,
        "get_meter",
        lambda name, version: calls.append((name, version)) or meter,
    )

    sink = OpenTelemetryUsageSink()

    assert sink.meter is meter
    assert calls == [("techrevati.runtime", "9.9.9")]


def test_otel_event_sink_emits_invoke_agent_span(in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = OpenTelemetrySink(tracer=tracer, agent_id="abc")
    orch = AgentSession(role="writer", phase="draft", event_sink=sink)

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


def test_otel_event_sink_rejects_invalid_config_and_event(in_memory_tracer):
    tracer, _ = in_memory_tracer
    with pytest.raises(ValueError, match="provider_name"):
        OpenTelemetrySink(tracer=tracer, provider_name="")
    with pytest.raises(ValueError, match="agent_id"):
        OpenTelemetrySink(tracer=tracer, agent_id="")
    with pytest.raises(TypeError, match="include_event_detail"):
        OpenTelemetrySink(tracer=tracer, include_event_detail="yes")  # type: ignore[arg-type]

    sink = OpenTelemetrySink(tracer=tracer)
    with pytest.raises(TypeError, match="AgentEvent"):
        sink.emit(object())  # type: ignore[arg-type]


def test_otel_event_sink_skips_non_finite_data_attributes(in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = OpenTelemetrySink(tracer=tracer)
    sink.emit(
        AgentEvent.blocked(
            "writer",
            "draft",
            data={"ok": 1, "bad": float("nan"), "also_bad": float("inf")},
        )
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = spans[0].attributes
    assert attrs is not None
    assert attrs.get("techrevati.data.ok") == 1
    assert "techrevati.data.bad" not in attrs
    assert "techrevati.data.also_bad" not in attrs


def test_otel_event_sink_sets_error_type_on_failure(in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = OpenTelemetrySink(tracer=tracer)
    orch = AgentSession(role="writer", phase="draft", event_sink=sink)

    with pytest.raises(RuntimeError):
        with orch.session() as session:
            session.run_turn(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    spans = exporter.get_finished_spans()
    error_spans = [s for s in spans if s.attributes.get("error.type")]
    assert error_spans, "expected at least one span with error.type"


def test_otel_event_sink_omits_event_detail_by_default(in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = OpenTelemetrySink(tracer=tracer)
    sink.emit(
        AgentEvent.failed(
            "writer",
            "draft",
            runtime_pkg.AgentFailureClass.TOOL_ERROR,
            detail="secret prompt fragment",
        )
    )

    span = exporter.get_finished_spans()[0]
    assert "techrevati.detail" not in span.attributes
    assert span.status.description == "tool_error"


def test_otel_event_sink_can_opt_in_to_event_detail(in_memory_tracer):
    tracer, exporter = in_memory_tracer
    sink = OpenTelemetrySink(tracer=tracer, include_event_detail=True)
    sink.emit(AgentEvent.recovery_attempted("writer", "draft", detail="safe summary"))

    span = exporter.get_finished_spans()[0]
    assert span.attributes.get("techrevati.detail") == "safe summary"


def test_otel_usage_sink_records_token_and_cost_metrics(in_memory_meter):
    meter, reader = in_memory_meter
    usage_sink = OpenTelemetryUsageSink(meter=meter)
    orch = AgentSession(role="writer", phase="draft", usage_sink=usage_sink)

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


def test_otel_usage_sink_rejects_invalid_config_and_records(in_memory_meter):
    meter, _ = in_memory_meter
    with pytest.raises(ValueError, match="provider_name"):
        OpenTelemetryUsageSink(meter=meter, provider_name="")

    sink = OpenTelemetryUsageSink(meter=meter)
    with pytest.raises(ValueError, match="model"):
        sink.record("", UsageSnapshot(input_tokens=1), 0.001)
    with pytest.raises(TypeError, match="usage"):
        sink.record("m", object(), 0.001)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=1), False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=1), -0.001)
    with pytest.raises(ValueError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=1), float("nan"))


def test_otel_sink_raises_clear_error_when_otel_missing(monkeypatch):
    """If opentelemetry isn't installed, instantiating the sink should
    raise an ImportError with a clear hint."""
    import techrevati.runtime.otel as otel_mod

    monkeypatch.setattr(otel_mod, "_OTEL_AVAILABLE", False)
    monkeypatch.setattr(otel_mod, "_OTEL_IMPORT_ERROR", ImportError("simulated"))
    with pytest.raises(ImportError, match=r"techrevati-runtime\[otel\]"):
        OpenTelemetrySink()
