"""
OpenTelemetry integration — GenAI semantic-conventions-aligned sink.

This module is import-safe only if the ``[otel]`` extra is installed.
At import time it tries to load ``opentelemetry`` packages; if they
are missing, a clear ``ImportError`` is raised so callers learn what
to install instead of getting an obscure ``AttributeError`` later.

The sink emits **OpenTelemetry GenAI semantic conventions** attributes
(https://opentelemetry.io/docs/specs/semconv/gen-ai/) so any GenAI-aware
APM ingest (the same one consuming OpenAI Agents SDK telemetry)
will surface our runtime as a first-class agent.

What gets emitted in 0.2.0 (v2 — agent-level nesting):
- ``AGENT_STARTED`` / ``PHASE_STARTED`` open a long-lived parent span
  and stash it on the sink keyed by ``(role, phase)``.
- All other events emit one-shot spans **as children** of the matching
  open parent (if any) via OTel context propagation, so a typical
  session produces a `invoke_agent` root with `execute_tool` /
  recovery siblings nested under it.
- ``AGENT_COMPLETED`` / ``AGENT_FAILED`` / ``PHASE_COMPLETED`` end the
  parent span, copying the terminal event's attributes onto it (incl.
  ``error.type`` and a ``Status(StatusCode.ERROR, ...)`` on failure).

Full tool-call-level nesting (``invoke_agent`` > ``invoke_agent`` per
turn > ``execute_tool`` per tool) is still a 0.3.0 item; today every
non-parent event is a leaf under the agent span.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from techrevati.runtime.agent_events import AgentEvent, AgentEventName
from techrevati.runtime.usage_tracking import UsageSnapshot

try:
    from opentelemetry import metrics, trace
    from opentelemetry.metrics import Counter, Histogram, Meter
    from opentelemetry.trace import Span, Status, StatusCode, Tracer

    _OTEL_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - import guard
    _OTEL_AVAILABLE = False
    _OTEL_IMPORT_ERROR: ImportError | None = exc
else:
    _OTEL_IMPORT_ERROR = None

if TYPE_CHECKING:  # pragma: no cover - type-only
    from opentelemetry.metrics import Counter, Histogram, Meter
    from opentelemetry.trace import Span, Tracer


# Provider name surfaced on every span/metric. Override per-instance
# if you wrap a specific upstream (e.g. provider_name="openai").
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


_PARENT_OPEN_EVENTS = frozenset(
    {
        AgentEventName.AGENT_STARTED.value,
        AgentEventName.PHASE_STARTED.value,
    }
)

_PARENT_CLOSE_EVENTS = frozenset(
    {
        AgentEventName.AGENT_COMPLETED.value,
        AgentEventName.AGENT_FAILED.value,
        AgentEventName.PHASE_COMPLETED.value,
    }
)


@dataclass
class OpenTelemetrySink:
    """EventSink that mirrors AgentEvents into nested OTel spans.

    Span names follow GenAI operation naming
    (https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/).
    ``AGENT_STARTED`` / ``PHASE_STARTED`` open a long-lived parent span
    keyed by ``(role, phase)``; subsequent events emit as children of
    that parent until ``AGENT_COMPLETED`` / ``AGENT_FAILED`` /
    ``PHASE_COMPLETED`` end it.

    Pass an explicit ``tracer`` to avoid pulling the global tracer
    (useful for tests with an in-memory exporter).
    """

    tracer: Tracer | None = None
    provider_name: str = DEFAULT_PROVIDER_NAME
    agent_id: str | None = None
    _active_spans: dict[tuple[str, str], Span] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_otel()
        if self.tracer is None:
            self.tracer = trace.get_tracer("techrevati.runtime", "0.2.0")

    @staticmethod
    def _span_key(event: AgentEvent) -> tuple[str, str]:
        return (event.role or "", event.phase or "")

    def _populate(self, span: Span, event: AgentEvent, op: str) -> None:
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

    def emit(self, event: AgentEvent) -> None:
        assert self.tracer is not None  # set in __post_init__
        op = _OPERATION_NAME_MAP.get(event.event.value, "invoke_agent")
        span_name = f"{op} {event.role}" if event.role else op
        key = self._span_key(event)

        if event.event.value in _PARENT_OPEN_EVENTS:
            # Open a long-lived parent span. If a span is somehow
            # already open for this key (orchestrator restart, caller
            # bug), end it first so we don't leak.
            if key in self._active_spans:
                self._active_spans.pop(key).end()
            new_parent = self.tracer.start_span(span_name)
            self._populate(new_parent, event, op)
            self._active_spans[key] = new_parent
            return

        if event.event.value in _PARENT_CLOSE_EVENTS:
            if key in self._active_spans:
                parent = self._active_spans.pop(key)
                # Copy terminal-event attributes onto the parent so the
                # final span carries the failure_class / detail; then end.
                self._populate(parent, event, op)
                if event.failure_class is not None:
                    parent.set_status(
                        Status(StatusCode.ERROR, event.detail or "failed")
                    )
                parent.end()
                return
            # No matching open parent — fall through to a one-shot
            # leaf so the event still surfaces in traces.

        # Leaf event. Emit as a child of the active parent if there is
        # one; otherwise let OTel create a root.
        active = self._active_spans.get(key)
        if active is not None:
            ctx = trace.set_span_in_context(active)
            with self.tracer.start_as_current_span(span_name, context=ctx) as span:
                self._populate(span, event, op)
        else:
            with self.tracer.start_as_current_span(span_name) as span:
                self._populate(span, event, op)


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
