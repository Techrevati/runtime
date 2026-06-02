"""
Telemetry integration for runtime events and usage.

This module is import-safe only if the ``[otel]`` extra is installed.
At import time it tries to load ``opentelemetry`` packages; if they
are missing, a clear ``ImportError`` is raised so callers learn what
to install instead of getting an obscure ``AttributeError`` later.

The sink emits semantic-convention attributes so telemetry backends can surface
runtime sessions as first-class agent activity.

What gets emitted:
- ``AGENT_STARTED`` / ``PHASE_STARTED`` open a long-lived parent span
  and stash it on the sink keyed by ``(role, phase)``.
- ``AGENT_TOOL_CALLED`` opens a tool span as a child of the active
  agent span. ``AGENT_TOOL_COMPLETED`` or a tool-scoped
  ``AGENT_FAILED`` event closes that tool span.
- Non-terminal failure events with structured data (for example a
  catchable usage-limit overrun or tool failure) are emitted as child
  spans and do not close the agent parent.
- ``AGENT_COMPLETED`` / ``AGENT_FAILED`` / ``PHASE_COMPLETED`` end the
  parent span, copying the terminal event's attributes onto it. Failure classes
  map to ``error.type`` and ``Status(StatusCode.ERROR, ...)`` except
  caller-driven cancellations, which remain typed but are not marked as errors.
"""

from __future__ import annotations

import atexit
import json
import logging
import math
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

from techrevati.runtime.agent_events import (
    AgentEvent,
    AgentEventName,
    AgentFailureClass,
)
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


# Provider name surfaced on every span/metric. Override per instance when
# wrapping a specific upstream.
DEFAULT_PROVIDER_NAME = "techrevati"
_DISTRIBUTION_NAME = "techrevati-runtime"
_INSTRUMENTATION_SCOPE_NAME = "techrevati.runtime"
logger = logging.getLogger(__name__)


def _validate_non_empty_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_optional_non_empty_str(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _validate_non_empty_str(field_name, value)


def _validate_bool(field_name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


def _validate_event(event: AgentEvent) -> AgentEvent:
    if not isinstance(event, AgentEvent):
        raise TypeError("event must be an AgentEvent")
    return event


def _is_error_failure_class(failure_class: AgentFailureClass) -> bool:
    return failure_class != AgentFailureClass.CANCELLED


def _validate_usage(usage: UsageSnapshot) -> UsageSnapshot:
    if not isinstance(usage, UsageSnapshot):
        raise TypeError("usage must be a UsageSnapshot")
    return usage


def _validate_cost_usd(cost_usd: float) -> float:
    if isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float)):
        raise TypeError("cost_usd must be a number")
    cost = float(cost_usd)
    if not math.isfinite(cost):
        raise ValueError("cost_usd must be finite")
    if cost < 0:
        raise ValueError("cost_usd must be non-negative")
    return cost


# GenAI semantic-convention message-body keys. When a caller puts these in an
# event payload, they are emitted as span events (not flattened attributes).
_GEN_AI_MESSAGE_KEYS = ("gen_ai.input.messages", "gen_ai.output.messages")


def _is_safe_attribute_value(value: Any) -> bool:
    if isinstance(value, bool | str | int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _render_messages(value: Any) -> str:
    """Render a GenAI message body to a span-event-safe string."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _instrumentation_version() -> str:
    """Return the installed package version for OTel instrumentation scope."""
    try:
        return _pkg_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return "0.0.0+local"


def _log_cleanup_failure(*, phase: str, exc: Exception) -> None:
    """Report cleanup failures without exposing raw exception messages."""
    with suppress(Exception):
        logger.error(
            "OpenTelemetry cleanup failed",
            extra={"phase": phase, "error_type": type(exc).__name__},
        )


# Mapping from our AgentEventName to semantic operation names.
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
            "Telemetry sink requires the [otel] extra. "
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
        AgentEventName.PHASE_COMPLETED.value,
    }
)

_TOOL_OPEN_EVENT = AgentEventName.AGENT_TOOL_CALLED.value
_TOOL_CLOSE_EVENT = AgentEventName.AGENT_TOOL_COMPLETED.value


# Process-wide registry of live sink instances. Used by the atexit hook below
# to flush orphan parent spans on abrupt termination.
# WeakSet so that sinks the user has dropped don't pin themselves in
# memory until interpreter shutdown.
_LIVE_SINKS: weakref.WeakSet[OpenTelemetrySink] = weakref.WeakSet()


def _flush_orphan_parent_spans_at_exit() -> None:
    """Close any parent spans the runtime didn't get a chance to close.

    If a process dies between ``AGENT_STARTED`` and ``AGENT_COMPLETED``
    — e.g. a crash, SIGTERM, or simply ``sys.exit()`` mid-session — the
    parent span otherwise stays open and the resulting trace is
    corrupted in the APM dashboard. This hook marks every still-open
    parent as ``ERROR`` with ``error.type=abrupt_termination`` and ends
    it so the trace tree closes cleanly.
    """
    for sink in list(_LIVE_SINKS):
        try:
            sink._close_orphan_parent_spans()
        except Exception as exc:  # noqa: BLE001 - atexit must never raise
            _log_cleanup_failure(phase="atexit_flush", exc=exc)


atexit.register(_flush_orphan_parent_spans_at_exit)


@dataclass(eq=False)
class OpenTelemetrySink:
    """EventSink that mirrors AgentEvents into nested telemetry spans.

    Span names follow semantic operation naming.
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
    include_event_detail: bool = False
    _active_spans: dict[tuple[str, str], Span] = field(
        default_factory=dict, init=False, repr=False
    )
    # A LIFO stack of spans per (role, phase, tool) key so concurrent calls to
    # the *same* tool each get their own span instead of the second call
    # force-closing the first as interrupted.
    _active_tool_spans: dict[tuple[str, str, str], list[Span]] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_otel()
        self.provider_name = _validate_non_empty_str(
            "provider_name", self.provider_name
        )
        self.agent_id = _validate_optional_non_empty_str("agent_id", self.agent_id)
        self.include_event_detail = _validate_bool(
            "include_event_detail", self.include_event_detail
        )
        if self.tracer is None:
            self.tracer = trace.get_tracer(
                _INSTRUMENTATION_SCOPE_NAME, _instrumentation_version()
            )
        _LIVE_SINKS.add(self)

    def _close_orphan_parent_spans(self) -> None:
        """Mark every still-open parent span as abruptly terminated and end it.

        Called by the ``atexit`` hook on interpreter shutdown. Safe to
        call multiple times — second invocation finds an empty dict.
        Never raises.
        """
        while self._active_tool_spans:
            _tool_key, tool_spans = self._active_tool_spans.popitem()
            for tool_span in tool_spans:
                self._end_span_with_error(
                    tool_span,
                    detail="process exited before agent.tool_completed",
                    error_type="abrupt_termination",
                )
        while self._active_spans:
            _parent_key, parent = self._active_spans.popitem()
            self._end_span_with_error(
                parent,
                detail="process exited before AGENT_COMPLETED / PHASE_COMPLETED",
                error_type="abrupt_termination",
            )

    @staticmethod
    def _span_key(event: AgentEvent) -> tuple[str, str]:
        return (event.role or "", event.phase or "")

    @staticmethod
    def _tool_name(event: AgentEvent) -> str | None:
        tool = (event.data or {}).get("tool")
        if isinstance(tool, str) and tool:
            return tool
        return None

    @staticmethod
    def _tool_key(event: AgentEvent) -> tuple[str, str, str] | None:
        tool = OpenTelemetrySink._tool_name(event)
        if tool is None:
            return None
        role, phase = OpenTelemetrySink._span_key(event)
        return (role, phase, tool)

    @staticmethod
    def _is_terminal_parent_close(event: AgentEvent) -> bool:
        if event.event.value in _PARENT_CLOSE_EVENTS:
            return True
        # ``agent.failed`` also represents non-terminal scoped failures:
        # tool body failures, usage-limit warnings caught by the caller,
        # budget warnings, etc. Session-level terminal failures are emitted
        # without structured data by ``AgentSession.session`` / ``asession``.
        # NB: this empty-data convention is deliberate and tested
        # (test_non_terminal_failed_event_with_data_does_not_close_parent) —
        # a failed event carrying data is a warning child, not a session end.
        return event.event.value == AgentEventName.AGENT_FAILED.value and not event.data

    @staticmethod
    def _operation_name(event: AgentEvent) -> str:
        if (
            event.event.value == AgentEventName.AGENT_FAILED.value
            and OpenTelemetrySink._tool_name(event) is not None
        ):
            return "execute_tool"
        return _OPERATION_NAME_MAP.get(event.event.value, "invoke_agent")

    @staticmethod
    def _span_name(event: AgentEvent, op: str) -> str:
        tool = OpenTelemetrySink._tool_name(event)
        if op == "execute_tool" and tool is not None:
            return f"{op} {tool}"
        return f"{op} {event.role}" if event.role else op

    @staticmethod
    def _end_span_with_error(span: Span, *, detail: str, error_type: str) -> None:
        try:
            span.set_attribute("error.type", error_type)
            span.set_status(Status(StatusCode.ERROR, detail))
            span.end()
        except Exception as exc:  # noqa: BLE001 - last-chance cleanup
            _log_cleanup_failure(phase="span_error_close", exc=exc)

    def _populate(self, span: Span, event: AgentEvent, op: str) -> None:
        span.set_attribute("gen_ai.operation.name", op)
        span.set_attribute("gen_ai.provider.name", self.provider_name)
        if event.role:
            span.set_attribute("gen_ai.agent.name", event.role)
        if self.agent_id:
            span.set_attribute("gen_ai.agent.id", self.agent_id)
        if event.phase:
            span.set_attribute("techrevati.phase", event.phase)
        if self.include_event_detail and event.detail:
            span.set_attribute("techrevati.detail", event.detail)
        if event.failure_class:
            span.set_attribute("techrevati.failure_class", event.failure_class.value)
            if _is_error_failure_class(event.failure_class):
                span.set_attribute("error.type", event.failure_class.value)
                status_detail = (
                    event.detail
                    if self.include_event_detail and event.detail
                    else event.failure_class.value
                )
                span.set_status(Status(StatusCode.ERROR, status_detail))
        for key, value in (event.data or {}).items():
            if key in _GEN_AI_MESSAGE_KEYS:
                # GenAI semconv message bodies are sensitive content; emit them
                # as span events only when detail capture is explicitly enabled.
                # The runtime does not own the model call, so these are present
                # only when the caller puts them in the event payload.
                if self.include_event_detail:
                    span.add_event(key, attributes={key: _render_messages(value)})
                continue
            if _is_safe_attribute_value(value):
                span.set_attribute(f"techrevati.data.{key}", value)

    def _close_active_tool_spans_for_key(
        self, key: tuple[str, str], *, detail: str
    ) -> None:
        for tool_key in list(self._active_tool_spans):
            if tool_key[:2] != key:
                continue
            for tool_span in self._active_tool_spans.pop(tool_key):
                self._end_span_with_error(
                    tool_span,
                    detail=detail,
                    error_type="tool_span_interrupted",
                )

    def _open_tool_span(
        self,
        event: AgentEvent,
        *,
        op: str,
        span_name: str,
    ) -> bool:
        assert self.tracer is not None
        tool_key = self._tool_key(event)
        if tool_key is None:
            return False

        # Each tool call is a child of the agent/phase parent (sibling to any
        # other in-flight call of the same tool), pushed onto the per-key stack.
        parent = self._active_spans.get(tool_key[:2])
        if parent is None:
            tool_span = self.tracer.start_span(span_name)
        else:
            ctx = trace.set_span_in_context(parent)
            tool_span = self.tracer.start_span(span_name, context=ctx)
        self._populate(tool_span, event, op)
        self._active_tool_spans.setdefault(tool_key, []).append(tool_span)
        return True

    def _close_tool_span(
        self,
        event: AgentEvent,
        *,
        op: str,
    ) -> bool:
        tool_key = self._tool_key(event)
        if tool_key is None:
            return False
        stack = self._active_tool_spans.get(tool_key)
        if not stack:
            return False
        # Close the most recently opened call for this tool (LIFO). Identical
        # concurrent calls are interchangeable, so LIFO pairing is correct for
        # span lifecycle and count.
        tool_span = stack.pop()
        if not stack:
            del self._active_tool_spans[tool_key]
        self._populate(tool_span, event, op)
        tool_span.end()
        return True

    def _leaf_parent(self, event: AgentEvent) -> Span | None:
        tool_key = self._tool_key(event)
        if tool_key is not None and self._active_tool_spans.get(tool_key):
            return self._active_tool_spans[tool_key][-1]
        return self._active_spans.get(self._span_key(event))

    def emit(self, event: AgentEvent) -> None:
        event = _validate_event(event)
        assert self.tracer is not None  # set in __post_init__
        op = self._operation_name(event)
        span_name = self._span_name(event, op)
        key = self._span_key(event)

        if event.event.value in _PARENT_OPEN_EVENTS:
            # Open a long-lived parent span. If a span is somehow
            # already open for this key (orchestrator restart, caller
            # bug), end it first so we don't leak.
            if key in self._active_spans:
                self._close_active_tool_spans_for_key(
                    key, detail="parent span restarted before tool completed"
                )
                self._active_spans.pop(key).end()
            new_parent = self.tracer.start_span(span_name)
            self._populate(new_parent, event, op)
            self._active_spans[key] = new_parent
            return

        if event.event.value == _TOOL_OPEN_EVENT and self._open_tool_span(
            event,
            op=op,
            span_name=span_name,
        ):
            return

        if event.event.value == _TOOL_CLOSE_EVENT and self._close_tool_span(
            event,
            op=op,
        ):
            return

        if (
            event.event.value == AgentEventName.AGENT_FAILED.value
            and self._tool_name(event) is not None
            and self._close_tool_span(event, op=op)
        ):
            return

        if self._is_terminal_parent_close(event):
            if key in self._active_spans:
                self._close_active_tool_spans_for_key(
                    key, detail="parent span closed before tool completed"
                )
                parent = self._active_spans.pop(key)
                # Copy terminal-event attributes onto the parent so the
                # final span carries the failure_class / detail; then end.
                self._populate(parent, event, op)
                parent.end()
                return
            # No matching open parent — fall through to a one-shot
            # leaf so the event still surfaces in traces.

        # Leaf event. Emit as a child of the active tool span when the event is
        # tool-scoped, otherwise as a child of the active agent/phase parent.
        active = self._leaf_parent(event)
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
        self.provider_name = _validate_non_empty_str(
            "provider_name", self.provider_name
        )
        if self.meter is None:
            self.meter = metrics.get_meter(
                _INSTRUMENTATION_SCOPE_NAME, _instrumentation_version()
            )
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
        model = _validate_non_empty_str("model", model)
        usage = _validate_usage(usage)
        cost_usd = _validate_cost_usd(cost_usd)
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
