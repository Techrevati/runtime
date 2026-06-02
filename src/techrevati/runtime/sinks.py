"""
Sinks — pluggable observability outputs for the runtime.

The orchestrator emits two streams while running:

- ``AgentEvent`` lifecycle events (state transitions, recovery, gate,
  permission, handoff, budget). These go to an ``EventSink``.
- Per-turn usage (model + ``UsageSnapshot`` + cost). These go to a
  ``UsageSink``.

Both protocols are tiny and synchronous. Default implementations buffer in
memory with a bounded ring so long-running sessions can't balloon. Durable and
cross-process observability can be added through separate sink implementations.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from techrevati.runtime._internal import (
    _validate_bool,
    _validate_cost_usd,
    _validate_model,
)
from techrevati.runtime.agent_events import AgentEvent
from techrevati.runtime.usage_tracking import UsageSnapshot

DEFAULT_RING_CAPACITY = 1000
logger = logging.getLogger(__name__)


def _validate_capacity(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("capacity must be an integer")
    if value <= 0:
        raise ValueError("capacity must be positive")
    return value


def _validate_event(event: AgentEvent) -> AgentEvent:
    if not isinstance(event, AgentEvent):
        raise TypeError("event must be an AgentEvent")
    return event


def _copy_event(event: AgentEvent) -> AgentEvent:
    return AgentEvent.from_dict(event.to_dict())


def _validate_usage(usage: UsageSnapshot) -> UsageSnapshot:
    if not isinstance(usage, UsageSnapshot):
        raise TypeError("usage must be a UsageSnapshot")
    return usage


def _copy_usage(usage: UsageSnapshot) -> UsageSnapshot:
    return UsageSnapshot.from_dict(usage.to_dict())


@runtime_checkable
class EventSink(Protocol):
    """Receives every ``AgentEvent`` the runtime produces."""

    def emit(self, event: AgentEvent) -> None: ...


@runtime_checkable
class UsageSink(Protocol):
    """Receives per-turn usage tuples (model, snapshot, cost in USD)."""

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None: ...


def _validate_event_sinks(sinks: Iterable[EventSink]) -> tuple[EventSink, ...]:
    try:
        entries = tuple(sinks)
    except TypeError as exc:
        raise TypeError("sinks must be an iterable of EventSink") from exc
    if not entries:
        raise ValueError("sinks must contain at least one EventSink")
    for sink in entries:
        if not isinstance(sink, EventSink):
            raise TypeError("all sinks must implement EventSink")
    return entries


def _validate_usage_sinks(sinks: Iterable[UsageSink]) -> tuple[UsageSink, ...]:
    try:
        entries = tuple(sinks)
    except TypeError as exc:
        raise TypeError("sinks must be an iterable of UsageSink") from exc
    if not entries:
        raise ValueError("sinks must contain at least one UsageSink")
    for sink in entries:
        if not isinstance(sink, UsageSink):
            raise TypeError("all sinks must implement UsageSink")
    return entries


def _log_fanout_failure(*, sink_kind: str, sink: object, exc: Exception) -> None:
    logger.error(
        "%s fanout sink raised; continuing fanout",
        sink_kind,
        extra={
            "sink_type": type(sink).__name__,
            "error_type": type(exc).__name__,
        },
    )


@dataclass
class NoopEventSink:
    """Validate and discard every event.

    This keeps no-op behavior observability-free while still enforcing the
    same protocol boundary as concrete sinks.
    """

    def emit(self, event: AgentEvent) -> None:
        _validate_event(event)
        return None


@dataclass
class NoopUsageSink:
    """Validate and discard every usage record."""

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        _validate_model(model)
        _validate_usage(usage)
        _validate_cost_usd(cost_usd)
        return None


@dataclass
class FanoutEventSink:
    """Forward each event to multiple event sinks.

    The fan-out attempts every configured sink even when one fails. By default
    it re-raises the first sink exception after fan-out so ``AgentSession`` can
    record its normal local diagnostic event while still keeping the session
    alive.
    """

    sinks: Iterable[EventSink]
    suppress_errors: bool = False

    def __post_init__(self) -> None:
        self.sinks = _validate_event_sinks(self.sinks)
        self.suppress_errors = _validate_bool("suppress_errors", self.suppress_errors)

    def emit(self, event: AgentEvent) -> None:
        event = _validate_event(event)
        first_error: Exception | None = None
        for sink in self.sinks:
            try:
                sink.emit(_copy_event(event))
            except Exception as exc:  # noqa: BLE001 - fan-out boundary
                if first_error is None:
                    first_error = exc
                _log_fanout_failure(sink_kind="event", sink=sink, exc=exc)
        if first_error is not None and not self.suppress_errors:
            raise first_error


@dataclass
class FanoutUsageSink:
    """Forward each usage record to multiple usage sinks."""

    sinks: Iterable[UsageSink]
    suppress_errors: bool = False

    def __post_init__(self) -> None:
        self.sinks = _validate_usage_sinks(self.sinks)
        self.suppress_errors = _validate_bool("suppress_errors", self.suppress_errors)

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        model = _validate_model(model)
        usage = _validate_usage(usage)
        cost_usd = _validate_cost_usd(cost_usd)
        first_error: Exception | None = None
        for sink in self.sinks:
            try:
                sink.record(model, _copy_usage(usage), cost_usd)
            except Exception as exc:  # noqa: BLE001 - fan-out boundary
                if first_error is None:
                    first_error = exc
                _log_fanout_failure(sink_kind="usage", sink=sink, exc=exc)
        if first_error is not None and not self.suppress_errors:
            raise first_error


@dataclass
class RingBufferEventSink:
    """In-memory bounded ring of recent events.

    Useful for tests, debug consoles, and short-lived processes. The
    buffer drops oldest entries silently once ``capacity`` is reached;
    if you need durability, plug in a persistent sink or write a custom one.
    """

    capacity: int = DEFAULT_RING_CAPACITY
    _events: deque[AgentEvent] = field(init=False, repr=False)
    _lock: Any = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.capacity = _validate_capacity(self.capacity)
        self._events = deque(maxlen=self.capacity)

    def emit(self, event: AgentEvent) -> None:
        event = _validate_event(event)
        with self._lock:
            self._events.append(_copy_event(event))

    @property
    def events(self) -> list[AgentEvent]:
        with self._lock:
            return [_copy_event(event) for event in self._events]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


@dataclass
class RingBufferUsageSink:
    """In-memory bounded ring of recent usage records."""

    capacity: int = DEFAULT_RING_CAPACITY
    _records: deque[tuple[str, UsageSnapshot, float]] = field(init=False, repr=False)
    _lock: Any = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.capacity = _validate_capacity(self.capacity)
        self._records = deque(maxlen=self.capacity)

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        model = _validate_model(model)
        usage = _validate_usage(usage)
        cost_usd = _validate_cost_usd(cost_usd)
        with self._lock:
            self._records.append((model, _copy_usage(usage), cost_usd))

    @property
    def records(self) -> list[tuple[str, UsageSnapshot, float]]:
        with self._lock:
            return [
                (model, _copy_usage(usage), cost_usd)
                for model, usage, cost_usd in self._records
            ]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


__all__ = [
    "DEFAULT_RING_CAPACITY",
    "EventSink",
    "FanoutEventSink",
    "FanoutUsageSink",
    "NoopEventSink",
    "NoopUsageSink",
    "RingBufferEventSink",
    "RingBufferUsageSink",
    "UsageSink",
]
