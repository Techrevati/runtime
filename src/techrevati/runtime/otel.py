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
  and stash it on the sink keyed by ``(role, phase)``. An agent span nests under
  the open span of its phase, when there is one.
- ``AGENT_TOOL_CALLED`` opens a tool span as a child of the active
  agent span. ``AGENT_TOOL_COMPLETED``, a tool-scoped ``AGENT_FAILED`` event, or a
  post-execution guardrail ``AGENT_BLOCKED`` event closes that tool span.
- ``AGENT_TURN_COMPLETED`` / ``AGENT_TURN_FAILED`` emit a ``chat {model}`` span
  backdated by the turn's measured duration.
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
import threading
import time
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

from techrevati.runtime._internal import (
    _validate_bool,
    _validate_cost_usd,
    _validate_non_empty_str,
    _validate_optional_non_empty_str,
)
from techrevati.runtime.agent_events import (
    AgentEvent,
    AgentEventName,
    AgentFailureClass,
)
from techrevati.runtime.usage_tracking import UsageSnapshot

try:
    from opentelemetry import metrics, trace
    from opentelemetry.metrics import Counter, Histogram, Meter
    from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

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


# GenAI semantic-convention message-body keys. When a caller puts these in an
# event payload, they are emitted as span events (not flattened attributes).
_GEN_AI_MESSAGE_KEYS = ("gen_ai.input.messages", "gen_ai.output.messages")


def _is_safe_attribute_value(value: Any) -> bool:
    if isinstance(value, bool | str | int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _duration_seconds(event: AgentEvent) -> float | None:
    value = (event.data or {}).get("duration_seconds")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _duration_histogram(meter: Meter, name: str, description: str) -> Histogram:
    try:
        return meter.create_histogram(
            name=name,
            unit="s",
            description=description,
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS_SECONDS,
        )
    except TypeError:
        # The advisory parameter is newer than the [otel] floor; the SDK's default
        # buckets are the only option there.
        return meter.create_histogram(name=name, unit="s", description=description)


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
    AgentEventName.AGENT_STARTED.value: "invoke_agent",
    AgentEventName.AGENT_READY.value: "invoke_agent",
    AgentEventName.AGENT_BLOCKED.value: "invoke_agent",
    AgentEventName.AGENT_TOOL_CALLED.value: "execute_tool",
    AgentEventName.AGENT_TOOL_COMPLETED.value: "execute_tool",
    AgentEventName.AGENT_COMPLETED.value: "invoke_agent",
    AgentEventName.AGENT_FAILED.value: "invoke_agent",
    AgentEventName.AGENT_TURN_COMPLETED.value: "chat",
    AgentEventName.AGENT_TURN_FAILED.value: "chat",
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
_TURN_EVENTS = frozenset(
    {
        AgentEventName.AGENT_TURN_COMPLETED.value,
        AgentEventName.AGENT_TURN_FAILED.value,
    }
)

# Boundaries the GenAI conventions specify for their duration histograms.
_DURATION_BUCKETS_SECONDS = (
    0.01,
    0.02,
    0.04,
    0.08,
    0.16,
    0.32,
    0.64,
    1.28,
    2.56,
    5.12,
    10.24,
    20.48,
    40.96,
    81.92,
)


# Process-wide registry of live sink instances. Used by the atexit hook below
# to flush orphan parent spans on abrupt termination.
# WeakSet so that sinks the user has dropped don't pin themselves in
# memory until interpreter shutdown.
_LIVE_SINKS: weakref.WeakSet[OpenTelemetrySink] = weakref.WeakSet()


def _flush_orphan_parent_spans_at_exit() -> None:
    """Close any parent spans the runtime didn't get a chance to close.

    If an interpreter exits normally between ``AGENT_STARTED`` and
    ``AGENT_COMPLETED`` — ``sys.exit()`` or an uncaught exception mid-session —
    the parent span otherwise stays open and the resulting trace is corrupted in
    the APM dashboard. This hook marks every still-open parent as ``ERROR`` with
    ``error.type=abrupt_termination`` and ends it.

    Limits: ``atexit`` never runs on a default SIGTERM, ``os._exit`` or a hard
    crash. A span ended here also reaches the exporter only if the
    ``TracerProvider`` shuts down afterwards, and ``atexit`` runs last-registered
    first, so create the provider before the first sink. For SIGTERM or a provider
    created later, call :meth:`OpenTelemetrySink.shutdown` yourself.
    """
    for sink in list(_LIVE_SINKS):
        try:
            sink._close_orphan_parent_spans()
        except Exception as exc:  # noqa: BLE001 - atexit must never raise
            _log_cleanup_failure(phase="atexit_flush", exc=exc)


_atexit_lock = threading.Lock()
_atexit_registered = False


def _ensure_atexit_flush_registered() -> None:
    """Register the flush on first sink creation, not at import.

    Registering late makes it run before the shutdown hook of a provider that
    already exists, so orphan spans are still exported by a batch processor.
    """
    global _atexit_registered
    with _atexit_lock:
        if not _atexit_registered:
            atexit.register(_flush_orphan_parent_spans_at_exit)
            _atexit_registered = True


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
        _ensure_atexit_flush_registered()

    def shutdown(self) -> None:
        """End every span still open, marking it ``abrupt_termination``.

        Call this from a SIGTERM handler or before shutting down the
        ``TracerProvider``; ``atexit`` alone covers neither case. Idempotent, and
        the sink stays usable afterwards.
        """
        self._close_orphan_parent_spans()

    def _close_orphan_parent_spans(self) -> None:
        """Mark every still-open parent span as abruptly terminated and end it.

        Called by ``shutdown()`` and the ``atexit`` hook. Safe to call multiple
        times — second invocation finds an empty dict. Never raises.
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
    def _is_post_execution_block(event: AgentEvent) -> bool:
        """A guardrail rejected a tool's result, so the call itself has finished.

        Pre-execution blocks carry another stage (or none) and have no open span
        of their own; closing on them could end a concurrent call of that tool.
        """
        return (
            event.event.value == AgentEventName.AGENT_BLOCKED.value
            and OpenTelemetrySink._tool_name(event) is not None
            and (event.data or {}).get("stage") == "post"
        )

    @staticmethod
    def _operation_name(event: AgentEvent) -> str:
        if (
            event.event.value == AgentEventName.AGENT_FAILED.value
            and OpenTelemetrySink._tool_name(event) is not None
        ) or OpenTelemetrySink._is_post_execution_block(event):
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
        tool = self._tool_name(event)
        if op == "execute_tool" and tool is not None:
            span.set_attribute("gen_ai.tool.name", tool)
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
        tool_span = self.tracer.start_span(
            span_name, context=self._parent_context(*tool_key[:2])
        )
        self._populate(tool_span, event, op)
        self._active_tool_spans.setdefault(tool_key, []).append(tool_span)
        return True

    def _parent_context(self, role: str, phase: str) -> Any:
        """Context of the agent's span, else its phase's; None means ambient."""
        parent = self._active_spans.get((role, phase))
        if parent is None:
            parent = self._active_spans.get(("", phase))
        return None if parent is None else trace.set_span_in_context(parent)

    def _emit_turn_span(self, event: AgentEvent, *, op: str) -> None:
        assert self.tracer is not None
        data = event.data or {}
        model = data.get("model")
        name = f"{op} {model}" if isinstance(model, str) and model else op
        duration = _duration_seconds(event)
        end_ns = time.time_ns()
        start_ns = end_ns - int((duration or 0.0) * 1e9)
        span = self.tracer.start_span(
            name,
            context=self._parent_context(*self._span_key(event)),
            kind=SpanKind.CLIENT,
            start_time=start_ns,
        )
        self._populate(span, event, op)
        if isinstance(model, str) and model:
            span.set_attribute("gen_ai.request.model", model)
        error_type = data.get("error_type")
        # A cancellation is the caller's choice, not an error (see the module header).
        if (
            event.event.value == AgentEventName.AGENT_TURN_FAILED.value
            and error_type != "cancelled"
        ):
            error = error_type if isinstance(error_type, str) else "unknown"
            span.set_attribute("error.type", error)
            span.set_status(Status(StatusCode.ERROR, error))
        span.end(end_time=end_ns)

    def _close_tool_span(
        self,
        event: AgentEvent,
        *,
        op: str,
        error_type: str | None = None,
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
        if error_type is not None:
            tool_span.set_attribute("error.type", error_type)
            tool_span.set_status(Status(StatusCode.ERROR, error_type))
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
            new_parent = self.tracer.start_span(
                span_name,
                context=None if not key[0] else self._parent_context("", key[1]),
            )
            self._populate(new_parent, event, op)
            self._active_spans[key] = new_parent
            return

        if event.event.value in _TURN_EVENTS:
            self._emit_turn_span(event, op=op)
            return

        if self._is_post_execution_block(event) and self._close_tool_span(
            event, op=op, error_type="guardrail_blocked"
        ):
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


_USAGE_COUNTERS = (
    ("input_tokens", "The number of input tokens used, including cached tokens."),
    ("output_tokens", "The number of output tokens used."),
    ("cache_read.input_tokens", "The number of input tokens served from a cache."),
    ("cache_write.input_tokens", "The number of input tokens written to a cache."),
)


@dataclass
class OpenTelemetryUsageSink:
    """UsageSink that records GenAI client metrics.

    Emits:
    - ``gen_ai.client.inference.usage.input_tokens`` (including cached tokens),
      ``.output_tokens``, ``.cache_read.input_tokens`` and
      ``.cache_write.input_tokens`` counters, as the GenAI conventions define them.
      ``gen_ai.token.modality`` is always ``unknown`` because ``UsageSnapshot``
      does not split tokens by modality; the conventions require that value here.
      ``gen_ai.operation.name`` is always ``chat``, the only inference operation
      the session reports.
    - ``gen_ai.client.token.usage`` histogram with a ``gen_ai.token.type``
      discriminator (``input`` / ``output``). The conventions have removed this
      metric; it stays on by default so existing dashboards keep working. Set
      ``emit_legacy_token_metric=False`` once they read the counters.
    - ``techrevati.cost.usd`` counter (custom — no standard GenAI cost
      metric yet).

    ``gen_ai.provider.name`` is required by the conventions but the runtime does
    not know who served a call, so set ``provider_name`` to the real provider; the
    default marks data as coming from this library instead.
    """

    meter: Meter | None = None
    provider_name: str = DEFAULT_PROVIDER_NAME
    emit_legacy_token_metric: bool = True
    _token_histogram: Histogram | None = field(default=None, init=False, repr=False)
    _cost_counter: Counter | None = field(default=None, init=False, repr=False)
    _usage_counters: dict[str, Counter] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        _require_otel()
        self.provider_name = _validate_non_empty_str(
            "provider_name", self.provider_name
        )
        self.emit_legacy_token_metric = _validate_bool(
            "emit_legacy_token_metric", self.emit_legacy_token_metric
        )
        if self.meter is None:
            self.meter = metrics.get_meter(
                _INSTRUMENTATION_SCOPE_NAME, _instrumentation_version()
            )
        if self.emit_legacy_token_metric:
            self._token_histogram = self.meter.create_histogram(
                name="gen_ai.client.token.usage",
                unit="{token}",
                description="Per-turn token usage by type",
            )
        for suffix, description in _USAGE_COUNTERS:
            self._usage_counters[suffix] = self.meter.create_counter(
                name=f"gen_ai.client.inference.usage.{suffix}",
                unit="{token}",
                description=description,
            )
        self._cost_counter = self.meter.create_counter(
            name="techrevati.cost.usd",
            unit="USD",
            description="Cumulative model spend in USD",
        )

    def _record_usage_counters(self, model: str, usage: UsageSnapshot) -> None:
        attrs: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider_name,
            "gen_ai.request.model": model,
            "gen_ai.token.modality": "unknown",
        }
        # UsageSnapshot counts cached tokens separately from input_tokens (each
        # bucket is priced once), while the convention's input counter includes
        # them and the cache counters are subsets of it.
        amounts = {
            "input_tokens": (
                usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
            ),
            "output_tokens": usage.output_tokens,
            "cache_read.input_tokens": usage.cache_read_tokens,
            "cache_write.input_tokens": usage.cache_write_tokens,
        }
        for suffix, amount in amounts.items():
            if amount:  # The conventions require strictly positive measurements.
                self._usage_counters[suffix].add(amount, attributes=attrs)

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        model = _validate_non_empty_str("model", model)
        usage = _validate_usage(usage)
        cost_usd = _validate_cost_usd(cost_usd)
        assert self._cost_counter is not None
        self._record_usage_counters(model, usage)
        if self._token_histogram is not None:
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
                self._token_histogram.record(
                    usage.output_tokens, attributes=attrs_output
                )
        if cost_usd:
            self._cost_counter.add(
                cost_usd,
                attributes={
                    "gen_ai.provider.name": self.provider_name,
                    "gen_ai.request.model": model,
                },
            )


@dataclass
class OpenTelemetryMetricsSink:
    """EventSink that records GenAI duration histograms from session events.

    Emits ``gen_ai.client.inference.duration`` for each model turn and
    ``gen_ai.execute_tool.duration`` for each tool call, in seconds, with the
    conventions' bucket boundaries and ``error.type`` on failure. Events without a
    usable ``duration_seconds`` are ignored. Use it next to
    :class:`OpenTelemetrySink`, for example through ``FanoutEventSink``.
    ``provider_name`` has the same caveat as on :class:`OpenTelemetryUsageSink`.
    """

    meter: Meter | None = None
    provider_name: str = DEFAULT_PROVIDER_NAME
    _inference_duration: Histogram | None = field(default=None, init=False, repr=False)
    _tool_duration: Histogram | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        _require_otel()
        self.provider_name = _validate_non_empty_str(
            "provider_name", self.provider_name
        )
        if self.meter is None:
            self.meter = metrics.get_meter(
                _INSTRUMENTATION_SCOPE_NAME, _instrumentation_version()
            )
        self._inference_duration = _duration_histogram(
            self.meter,
            "gen_ai.client.inference.duration",
            "GenAI client inference operation duration.",
        )
        self._tool_duration = _duration_histogram(
            self.meter,
            "gen_ai.execute_tool.duration",
            "The duration of a single tool execution.",
        )

    def emit(self, event: AgentEvent) -> None:
        event = _validate_event(event)
        duration = _duration_seconds(event)
        if duration is None:
            return
        if event.event.value in _TURN_EVENTS:
            self._record_turn(event, duration)
            return
        tool = OpenTelemetrySink._tool_name(event)
        if tool is not None and event.event.value in (
            AgentEventName.AGENT_TOOL_COMPLETED.value,
            AgentEventName.AGENT_FAILED.value,
            AgentEventName.AGENT_BLOCKED.value,
        ):
            self._record_tool(event, tool, duration)

    def _record_turn(self, event: AgentEvent, duration: float) -> None:
        assert self._inference_duration is not None
        data = event.data or {}
        attrs: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider_name,
        }
        model = data.get("model")
        if isinstance(model, str) and model:
            attrs["gen_ai.request.model"] = model
        error_type = data.get("error_type")
        if (
            event.event.value == AgentEventName.AGENT_TURN_FAILED.value
            and error_type != "cancelled"
        ):
            attrs["error.type"] = (
                error_type if isinstance(error_type, str) else "unknown"
            )
        self._inference_duration.record(duration, attributes=attrs)

    def _record_tool(self, event: AgentEvent, tool: str, duration: float) -> None:
        assert self._tool_duration is not None
        attrs: dict[str, Any] = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool,
        }
        if event.role:
            attrs["gen_ai.agent.name"] = event.role
        if event.failure_class and _is_error_failure_class(event.failure_class):
            attrs["error.type"] = event.failure_class.value
        if event.event.value == AgentEventName.AGENT_BLOCKED.value:
            attrs["error.type"] = "guardrail_blocked"
        self._tool_duration.record(duration, attributes=attrs)


__all__ = [
    "DEFAULT_PROVIDER_NAME",
    "OpenTelemetryMetricsSink",
    "OpenTelemetrySink",
    "OpenTelemetryUsageSink",
]
