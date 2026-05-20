"""
OpenTelemetry integration — GenAI semantic-conventions-aligned sink.

This module is import-safe only if the ``[otel]`` extra is installed.
At import time it tries to load ``opentelemetry`` packages; if they
are missing, a clear ``ImportError`` is raised so callers learn what
to install instead of getting an obscure ``AttributeError`` later.

The sink emits **OpenTelemetry GenAI semantic conventions** attributes
(https://opentelemetry.io/docs/specs/semconv/gen-ai/) so any GenAI-aware
APM ingest (the same one consuming Anthropic SDK / OpenAI Agents SDK)
will surface our runtime as a first-class agent.

What gets emitted today (v1):
- Every ``AgentEvent`` becomes a one-shot span named after the event
  with ``gen_ai.operation.name``, ``gen_ai.provider.name``,
  ``gen_ai.agent.id``, ``gen_ai.agent.name`` and (if applicable)
  ``error.type``.
- Every recorded turn writes ``gen_ai.client.token.usage`` and
  ``gen_ai.client.operation.duration`` histograms, plus a custom
  ``techrevati.cost.usd`` counter.

Nesting (parent/child spans) is intentionally NOT in this version —
discrete one-shot spans + ``gen_ai.agent.id`` give correlation, which
is enough for v1 dashboards. Span nesting will come in a follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from techrevati.runtime.agent_events import AgentEvent, AgentEventName
from techrevati.runtime.usage_tracking import UsageSnapshot

try:
    from opentelemetry import metrics, trace
    from opentelemetry.metrics import Counter, Histogram, Meter
    from opentelemetry.trace import Tracer

    _OTEL_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - import guard
    _OTEL_AVAILABLE = False
    _OTEL_IMPORT_ERROR: ImportError | None = exc
else:
    _OTEL_IMPORT_ERROR = None

if TYPE_CHECKING:  # pragma: no cover - type-only
    from opentelemetry.metrics import Counter, Histogram, Meter
    from opentelemetry.trace import Tracer


# Provider name surfaced on every span/metric. Override per-instance
# if you wrap a specific upstream (e.g. provider_name="anthropic").
DEFAULT_PROVIDER_NAME = "techrevati"

# Mapping from our AgentEventName to OTel GenAI operation name.
# Per https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
_OPERATION_NAME_MAP: dict[str, str] = {
    AgentEventName.AGENT_STARTED.value: "create_agent",
    AgentEventName.AGENT_READY.value: "create_agent",
    AgentEventName.AGENT_BLOCKED.value: "invoke_agent",
    AgentEventName.AGENT_TOOL_CALLED.value: "execute_tool",
    AgentEventName.AGENT_TOOL_COMPLETED.value: "execute_tool",
    AgentEventName.AGENT_COMPLETED.value: "invoke_agent",
    AgentEventName.AGENT_FAILED.value: "invoke_agent",
    AgentEventName.RECOVERY_ATTEMPTED.value: "invoke_agent",
    AgentEventName.RECOVERY_SUCCEEDED.value: "invoke_agent",
    AgentEventName.RECOVERY_FAILED.value: "invoke_agent",
    AgentEventName.RECOVERY_ESCALATED.value: "invoke_agent",
    AgentEventName.PHASE_STARTED.value: "invoke_workflow",
    AgentEventName.PHASE_COMPLETED.value: "invoke_workflow",
    AgentEventName.PHASE_GATE_PASSED.value: "invoke_agent",
    AgentEventName.PHASE_GATE_FAILED.value: "invoke_agent",
}


def _require_otel() -> None:
    if not _OTEL_AVAILABLE:
        raise ImportError(
            "OpenTelemetry sink requires the [otel] extra. "
            "Install with: pip install 'techrevati-runtime[otel]'"
        ) from _OTEL_IMPORT_ERROR


@dataclass
class OpenTelemetrySink:
    """EventSink that mirrors every AgentEvent as a one-shot OTel span.

    The span name follows the GenAI operation naming convention. Attributes
    are populated from the event's role/phase/detail plus optional context
    you can attach via ``agent_id`` and ``provider_name``.

    Pass an explicit ``tracer`` to avoid pulling the global tracer, useful
    for tests with an in-memory exporter.
    """

    tracer: Tracer | None = None
    provider_name: str = DEFAULT_PROVIDER_NAME
    agent_id: str | None = None

    def __post_init__(self) -> None:
        _require_otel()
        if self.tracer is None:
            self.tracer = trace.get_tracer("techrevati.runtime", "0.1.0")

    def emit(self, event: AgentEvent) -> None:
        op = _OPERATION_NAME_MAP.get(event.event.value, "invoke_agent")
        span_name = f"{op} {event.role}" if event.role else op
        assert self.tracer is not None  # set in __post_init__
        with self.tracer.start_as_current_span(span_name) as span:
            span.set_attribute("gen_ai.operation.name", op)
            span.set_attribute("gen_ai.provider.name", self.provider_name)
            if event.role:
                span.set_attribute("gen_ai.agent.name", event.role)
            if self.agent_id:
                span.set_attribute("gen_ai.agent.id", self.agent_id)
            if event.phase:
                span.set_attribute("techrevati.phase", event.phase)
            if event.detail:
                span.set_attribute("techrevati.detail", event.detail)
            if event.failure_class:
                span.set_attribute("error.type", event.failure_class.value)
            for key, value in (event.data or {}).items():
                if isinstance(value, (str, int, float, bool)):
                    span.set_attribute(f"techrevati.data.{key}", value)


@dataclass
class OpenTelemetryUsageSink:
    """UsageSink that records GenAI client metrics.

    Emits:
    - ``gen_ai.client.token.usage`` histogram with ``gen_ai.token.type``
      discriminator (``input`` / ``output``).
    - ``techrevati.cost.usd`` counter (custom — no standard GenAI cost
      metric yet).
    """

    meter: Meter | None = None
    provider_name: str = DEFAULT_PROVIDER_NAME
    _token_histogram: Histogram | None = field(default=None, init=False, repr=False)
    _cost_counter: Counter | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _require_otel()
        if self.meter is None:
            self.meter = metrics.get_meter("techrevati.runtime", "0.1.0")
        self._token_histogram = self.meter.create_histogram(
            name="gen_ai.client.token.usage",
            unit="{token}",
            description="Per-turn token usage by type",
        )
        self._cost_counter = self.meter.create_counter(
            name="techrevati.cost.usd",
            unit="USD",
            description="Cumulative model spend in USD",
        )

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        assert self._token_histogram is not None
        assert self._cost_counter is not None
        attrs_input: dict[str, Any] = {
            "gen_ai.provider.name": self.provider_name,
            "gen_ai.request.model": model,
            "gen_ai.token.type": "input",
        }
        attrs_output: dict[str, Any] = {
            **attrs_input,
            "gen_ai.token.type": "output",
        }
        if usage.input_tokens:
            self._token_histogram.record(usage.input_tokens, attributes=attrs_input)
        if usage.output_tokens:
            self._token_histogram.record(usage.output_tokens, attributes=attrs_output)
        if cost_usd:
            self._cost_counter.add(
                cost_usd,
                attributes={
                    "gen_ai.provider.name": self.provider_name,
                    "gen_ai.request.model": model,
                },
            )


__all__ = [
    "DEFAULT_PROVIDER_NAME",
    "OpenTelemetrySink",
    "OpenTelemetryUsageSink",
]
