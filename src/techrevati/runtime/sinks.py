"""
Sinks — pluggable observability outputs for the runtime.

The orchestrator emits two streams while running:

- ``AgentEvent`` lifecycle events (state transitions, recovery, gate,
  permission, handoff, budget). These go to an ``EventSink``.
- Per-turn usage (model + ``UsageSnapshot`` + cost). These go to a
  ``UsageSink``.

Both protocols are tiny and synchronous. Default implementations
buffer in memory with a bounded ring so long-running sessions can't
balloon. Cross-process / cross-host observability (OpenTelemetry,
Datadog, your own pipeline) ships as a separate sink that wraps these
protocols — see ``techrevati.runtime.otel``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from techrevati.runtime.agent_events import AgentEvent
from techrevati.runtime.usage_tracking import UsageSnapshot

DEFAULT_RING_CAPACITY = 1000


@runtime_checkable
class EventSink(Protocol):
    """Receives every ``AgentEvent`` the runtime produces."""

    def emit(self, event: AgentEvent) -> None: ...


@runtime_checkable
class UsageSink(Protocol):
    """Receives per-turn usage tuples (model, snapshot, cost in USD)."""

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None: ...


@dataclass
class NoopEventSink:
    """Discard every event. The default when no sink is configured."""

    def emit(self, event: AgentEvent) -> None:
        return None


@dataclass
class NoopUsageSink:
    """Discard every usage record. The default when no sink is configured."""

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        return None


@dataclass
class RingBufferEventSink:
    """In-memory bounded ring of recent events.

    Useful for tests, debug consoles, and short-lived processes. The
    buffer drops oldest entries silently once ``capacity`` is reached;
    if you need durability, plug in an OTel sink or write a custom one.
    """

    capacity: int = DEFAULT_RING_CAPACITY
    _events: deque[AgentEvent] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._events = deque(maxlen=self.capacity)

    def emit(self, event: AgentEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> list[AgentEvent]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


@dataclass
class RingBufferUsageSink:
    """In-memory bounded ring of recent usage records."""

    capacity: int = DEFAULT_RING_CAPACITY
    _records: deque[tuple[str, UsageSnapshot, float]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._records = deque(maxlen=self.capacity)

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        self._records.append((model, usage, cost_usd))

    @property
    def records(self) -> list[tuple[str, UsageSnapshot, float]]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()


__all__ = [
    "DEFAULT_RING_CAPACITY",
    "EventSink",
    "NoopEventSink",
    "NoopUsageSink",
    "RingBufferEventSink",
    "RingBufferUsageSink",
    "UsageSink",
]
