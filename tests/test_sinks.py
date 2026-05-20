"""Tests for techrevati.runtime.sinks (Sprint 4.1 + 4.2)."""

from __future__ import annotations

import logging

import pytest

from techrevati.runtime import (
    AgentEvent,
    AgentEventName,
    AgentEventStatus,
    ModelPricing,
    Orchestrator,
    RingBufferEventSink,
    RingBufferUsageSink,
    UsageSnapshot,
    register_pricing,
)
from techrevati.runtime.sinks import NoopEventSink, NoopUsageSink


@pytest.fixture(autouse=True)
def _register_pricing():
    register_pricing("test-model", ModelPricing(3.0, 15.0))


def _make_event() -> AgentEvent:
    return AgentEvent(
        event=AgentEventName.AGENT_STARTED,
        status=AgentEventStatus.RUNNING,
        role="r",
        phase="p",
    )


def test_ring_buffer_event_sink_records_in_order():
    sink = RingBufferEventSink(capacity=3)
    for _ in range(2):
        sink.emit(_make_event())
    assert len(sink.events) == 2


def test_ring_buffer_event_sink_drops_oldest_when_full():
    sink = RingBufferEventSink(capacity=2)
    e1, e2, e3 = _make_event(), _make_event(), _make_event()
    sink.emit(e1)
    sink.emit(e2)
    sink.emit(e3)
    assert len(sink.events) == 2
    assert sink.events[0] is e2
    assert sink.events[1] is e3


def test_noop_event_sink_discards():
    sink = NoopEventSink()
    sink.emit(_make_event())  # no error, no observable side effect


def test_ring_buffer_usage_sink_records_tuples():
    sink = RingBufferUsageSink(capacity=10)
    sink.record("m", UsageSnapshot(input_tokens=100), 0.001)
    assert sink.records == [("m", UsageSnapshot(input_tokens=100), 0.001)]


def test_noop_usage_sink_discards():
    sink = NoopUsageSink()
    sink.record("m", UsageSnapshot(input_tokens=100), 0.001)


# -- Integration: orchestrator forwards events + usage to sinks --


def test_orchestrator_emits_events_to_event_sink():
    sink = RingBufferEventSink()
    orch = Orchestrator(role="writer", phase="draft", event_sink=sink)
    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=100),
        )
    # At least the agent.completed event should arrive on the sink.
    assert len(sink.events) >= 1
    assert any(e.event.value == "agent.completed" for e in sink.events)


def test_orchestrator_records_usage_to_usage_sink():
    sink = RingBufferUsageSink()
    orch = Orchestrator(role="writer", phase="draft", usage_sink=sink)
    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=1000, output_tokens=500),
        )
    assert len(sink.records) == 1
    model, usage, cost = sink.records[0]
    assert model == "test-model"
    assert usage.input_tokens == 1000
    assert cost > 0


@pytest.mark.asyncio
async def test_async_orchestrator_emits_to_sinks():
    event_sink = RingBufferEventSink()
    usage_sink = RingBufferUsageSink()
    orch = Orchestrator(
        role="r", phase="p", event_sink=event_sink, usage_sink=usage_sink
    )

    async def call():
        return "ok"

    async with orch.asession() as session:
        await session.arun_turn(
            call, model="test-model", usage=UsageSnapshot(input_tokens=100)
        )

    assert len(event_sink.events) >= 1
    assert len(usage_sink.records) == 1


def test_misbehaving_event_sink_does_not_break_session(caplog):
    class _BoomSink:
        def emit(self, event: AgentEvent) -> None:
            raise RuntimeError("sink down")

    orch = Orchestrator(role="r", phase="p", event_sink=_BoomSink())
    with caplog.at_level(logging.ERROR, logger="techrevati.runtime.orchestrator"):
        with orch.session() as session:
            session.run_turn(lambda: "ok", model="test-model")
    assert session.worker.status.value == "completed"
    assert any("event_sink.emit raised" in r.getMessage() for r in caplog.records)


def test_misbehaving_usage_sink_does_not_break_session(caplog):
    class _BoomSink:
        def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
            raise RuntimeError("sink down")

    orch = Orchestrator(role="r", phase="p", usage_sink=_BoomSink())
    with caplog.at_level(logging.ERROR, logger="techrevati.runtime.orchestrator"):
        with orch.session() as session:
            session.run_turn(
                lambda: "ok",
                model="test-model",
                usage=UsageSnapshot(input_tokens=100),
            )
    assert session.worker.status.value == "completed"
    assert any("usage_sink.record raised" in r.getMessage() for r in caplog.records)
