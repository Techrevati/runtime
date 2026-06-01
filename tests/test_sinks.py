"""Tests for techrevati.runtime.sinks (Sprint 4.1 + 4.2)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

import techrevati.runtime.persistence as persistence
from techrevati.runtime import (
    AgentEvent,
    AgentEventName,
    AgentEventStatus,
    AgentFailureClass,
    AgentSession,
    ModelPricing,
    RingBufferEventSink,
    RingBufferUsageSink,
    SqliteEventSink,
    SqliteUsageSink,
    UsageSnapshot,
    register_pricing,
)
from techrevati.runtime.sinks import (
    FanoutEventSink,
    FanoutUsageSink,
    NoopEventSink,
    NoopUsageSink,
)


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
    assert sink.events[0] == e2
    assert sink.events[0] is not e2
    assert sink.events[1] == e3
    assert sink.events[1] is not e3


def test_ring_buffer_event_sink_snapshots_events_on_emit_and_read():
    sink = RingBufferEventSink(capacity=2)
    event = _make_event().with_data({"nested": {"values": [1]}})

    sink.emit(event)
    assert event.data is not None
    event.data["nested"]["values"].append(2)

    first_read = sink.events
    assert first_read[0].data == {"nested": {"values": [1]}}
    assert first_read[0] is not event

    assert first_read[0].data is not None
    first_read[0].data["nested"]["values"].append(3)
    second_read = sink.events
    assert second_read[0].data == {"nested": {"values": [1]}}


def test_ring_buffer_event_sink_rejects_invalid_config_and_events():
    with pytest.raises(TypeError, match="capacity"):
        RingBufferEventSink(capacity=cast(Any, True))
    with pytest.raises(ValueError, match="capacity"):
        RingBufferEventSink(capacity=0)

    sink = RingBufferEventSink()
    with pytest.raises(TypeError, match="AgentEvent"):
        sink.emit(cast(Any, object()))


def test_noop_event_sink_discards():
    sink = NoopEventSink()
    sink.emit(_make_event())  # no error, no observable side effect


def test_noop_event_sink_validates_event_shape():
    sink = NoopEventSink()
    with pytest.raises(TypeError, match="AgentEvent"):
        sink.emit(cast(Any, object()))


def test_ring_buffer_usage_sink_records_tuples():
    sink = RingBufferUsageSink(capacity=10)
    sink.record("m", UsageSnapshot(input_tokens=100), 0.001)
    assert sink.records == [("m", UsageSnapshot(input_tokens=100), 0.001)]


def test_ring_buffer_usage_sink_normalizes_model_name():
    sink = RingBufferUsageSink(capacity=10)
    sink.record(" m ", UsageSnapshot(input_tokens=100), 0.001)

    assert sink.records == [("m", UsageSnapshot(input_tokens=100), 0.001)]


def test_ring_buffer_usage_sink_snapshots_usage_on_record_and_read():
    sink = RingBufferUsageSink(capacity=2)
    usage = UsageSnapshot(input_tokens=100, output_tokens=50, cache_ttl="5m")

    sink.record("m", usage, 0.001)
    first_read = sink.records
    second_read = sink.records

    assert first_read[0] == ("m", usage, 0.001)
    assert first_read[0][1] is not usage
    assert first_read[0][1] is not second_read[0][1]


def test_ring_buffer_usage_sink_rejects_invalid_config_and_records():
    with pytest.raises(TypeError, match="capacity"):
        RingBufferUsageSink(capacity=cast(Any, True))
    with pytest.raises(ValueError, match="capacity"):
        RingBufferUsageSink(capacity=0)

    sink = RingBufferUsageSink()
    with pytest.raises(ValueError, match="model"):
        sink.record("", UsageSnapshot(input_tokens=100), 0.001)
    with pytest.raises(TypeError, match="usage"):
        sink.record("m", cast(Any, object()), 0.001)
    with pytest.raises(TypeError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=100), cast(Any, False))
    with pytest.raises(ValueError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=100), -0.001)
    with pytest.raises(ValueError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=100), float("nan"))
    with pytest.raises(ValueError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=100), float("inf"))


def test_noop_usage_sink_discards():
    sink = NoopUsageSink()
    sink.record("m", UsageSnapshot(input_tokens=100), 0.001)


def test_noop_usage_sink_validates_record_shape():
    sink = NoopUsageSink()
    with pytest.raises(ValueError, match="model"):
        sink.record("", UsageSnapshot(input_tokens=100), 0.001)
    with pytest.raises(TypeError, match="usage"):
        sink.record("m", cast(Any, object()), 0.001)
    with pytest.raises(TypeError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=100), cast(Any, False))
    with pytest.raises(ValueError, match="cost_usd"):
        sink.record("m", UsageSnapshot(input_tokens=100), float("nan"))


def test_fanout_event_sink_forwards_to_every_sink_and_copies_events():
    left = RingBufferEventSink()
    right = RingBufferEventSink()
    event = _make_event().with_data({"nested": {"values": [1]}})

    FanoutEventSink((left, right)).emit(event)
    assert event.data is not None
    event.data["nested"]["values"].append(2)

    assert left.events[0].data == {"nested": {"values": [1]}}
    assert right.events[0].data == {"nested": {"values": [1]}}
    assert left.events[0] is not right.events[0]


def test_fanout_event_sink_attempts_remaining_sinks_after_failure(caplog):
    class _BoomSink:
        def emit(self, event: AgentEvent) -> None:
            raise RuntimeError("event sink secret")

    survivor = RingBufferEventSink()
    fanout = FanoutEventSink((_BoomSink(), survivor))

    with caplog.at_level(logging.ERROR, logger="techrevati.runtime.sinks"):
        with pytest.raises(RuntimeError, match="event sink secret"):
            fanout.emit(_make_event())

    assert len(survivor.events) == 1
    assert any("event fanout sink raised" in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
    assert "event sink secret" not in caplog.text


def test_fanout_event_sink_can_suppress_errors():
    class _BoomSink:
        def emit(self, event: AgentEvent) -> None:
            raise RuntimeError("event sink secret")

    survivor = RingBufferEventSink()
    FanoutEventSink((_BoomSink(), survivor), suppress_errors=True).emit(_make_event())

    assert len(survivor.events) == 1


def test_fanout_usage_sink_forwards_to_every_sink_and_copies_usage():
    left = RingBufferUsageSink()
    right = RingBufferUsageSink()
    usage = UsageSnapshot(input_tokens=100, output_tokens=50)

    FanoutUsageSink((left, right)).record("m", usage, 0.001)

    assert left.records == [("m", usage, 0.001)]
    assert right.records == [("m", usage, 0.001)]
    assert left.records[0][1] is not right.records[0][1]


def test_fanout_usage_sink_attempts_remaining_sinks_after_failure(caplog):
    class _BoomSink:
        def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
            raise RuntimeError("usage sink secret")

    survivor = RingBufferUsageSink()
    fanout = FanoutUsageSink((_BoomSink(), survivor))

    with caplog.at_level(logging.ERROR, logger="techrevati.runtime.sinks"):
        with pytest.raises(RuntimeError, match="usage sink secret"):
            fanout.record("m", UsageSnapshot(input_tokens=100), 0.001)

    assert len(survivor.records) == 1
    assert any("usage fanout sink raised" in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
    assert "usage sink secret" not in caplog.text


def test_fanout_sinks_reject_invalid_config():
    with pytest.raises(ValueError, match="at least one"):
        FanoutEventSink(())
    with pytest.raises(TypeError, match="EventSink"):
        FanoutEventSink((cast(Any, object()),))
    with pytest.raises(TypeError, match="suppress_errors"):
        FanoutEventSink((NoopEventSink(),), suppress_errors=cast(Any, "yes"))

    with pytest.raises(ValueError, match="at least one"):
        FanoutUsageSink(())
    with pytest.raises(TypeError, match="UsageSink"):
        FanoutUsageSink((cast(Any, object()),))
    with pytest.raises(TypeError, match="suppress_errors"):
        FanoutUsageSink((NoopUsageSink(),), suppress_errors=cast(Any, "yes"))


# -- Integration: orchestrator forwards events + usage to sinks --


def test_orchestrator_emits_events_to_event_sink():
    sink = RingBufferEventSink()
    orch = AgentSession(role="writer", phase="draft", project_id=7, event_sink=sink)
    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            model="test-model",
            usage=UsageSnapshot(input_tokens=100),
        )
    assert sink.events[0].event.value == "agent.started"
    assert any(e.event.value == "agent.completed" for e in sink.events)
    assert all(e.project_id == 7 for e in sink.events)


def test_orchestrator_records_usage_to_usage_sink():
    sink = RingBufferUsageSink()
    orch = AgentSession(role="writer", phase="draft", usage_sink=sink)
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
    orch = AgentSession(
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
            raise RuntimeError("sink secret details")

    orch = AgentSession(role="r", phase="p", event_sink=_BoomSink())
    with caplog.at_level(logging.ERROR, logger="techrevati.runtime.orchestrator"):
        with orch.session() as session:
            session.run_turn(lambda: "ok", model="test-model")
    assert session.worker.status.value == "completed"
    assert any("event_sink.emit raised" in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
    assert "secret details" not in caplog.text
    diagnostics = [
        event
        for event in session.events
        if event.data and event.data.get("component") == "event_sink"
    ]
    assert diagnostics
    assert diagnostics[0].failure_class == AgentFailureClass.DEPENDENCY_FAILED
    assert diagnostics[0].detail == "event_sink failed; session continued"
    assert diagnostics[0].data == {
        "component": "event_sink",
        "error_type": "RuntimeError",
    }


def test_misbehaving_usage_sink_does_not_break_session(caplog):
    class _BoomSink:
        def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
            raise RuntimeError("usage secret details")

    orch = AgentSession(role="r", phase="p", usage_sink=_BoomSink())
    with caplog.at_level(logging.ERROR, logger="techrevati.runtime.orchestrator"):
        with orch.session() as session:
            session.run_turn(
                lambda: "ok",
                model="test-model",
                usage=UsageSnapshot(input_tokens=100),
            )
    assert session.worker.status.value == "completed"
    assert any("usage_sink.record raised" in r.getMessage() for r in caplog.records)
    assert all(r.exc_info is None for r in caplog.records)
    assert "secret details" not in caplog.text
    diagnostics = [
        event
        for event in session.events
        if event.data and event.data.get("component") == "usage_sink"
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].failure_class == AgentFailureClass.DEPENDENCY_FAILED
    assert diagnostics[0].detail == "usage_sink failed; session continued"
    assert diagnostics[0].data == {
        "component": "usage_sink",
        "error_type": "RuntimeError",
    }


def _metadata_version(db: Path, component: str) -> int | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT schema_version FROM techrevati_runtime_metadata"
            " WHERE component = ?",
            (component,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else int(row[0])


def _write_future_schema_version(db: Path, component: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE techrevati_runtime_metadata"
            " (component TEXT PRIMARY KEY, schema_version INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO techrevati_runtime_metadata"
            " (component, schema_version) VALUES (?, ?)",
            (component, 999),
        )
        conn.commit()
    finally:
        conn.close()


def test_open_wal_closes_connection_when_wal_setup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Connection:
        closed = False

        def execute(self, sql: str) -> None:
            assert sql == "PRAGMA journal_mode=WAL"
            raise sqlite3.OperationalError("wal unavailable")

        def close(self) -> None:
            self.closed = True

    opened: list[_Connection] = []

    def connect(path: str, *, check_same_thread: bool) -> _Connection:
        assert path == str(tmp_path / "events.db")
        assert check_same_thread is False
        conn = _Connection()
        opened.append(conn)
        return conn

    monkeypatch.setattr(persistence.sqlite3, "connect", connect)

    with pytest.raises(sqlite3.OperationalError, match="wal unavailable"):
        persistence._open_wal(tmp_path / "events.db")

    assert opened
    assert opened[0].closed is True


def test_sqlite_event_sink_records_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    sink = SqliteEventSink(db)
    sink.close()

    assert _metadata_version(db, "event_sink") == 1


def test_sqlite_event_sink_rejects_invalid_event_and_limit(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with SqliteEventSink(db) as sink:
        with pytest.raises(TypeError, match="AgentEvent"):
            sink.emit(cast(Any, object()))
        with pytest.raises(TypeError, match="limit"):
            list(sink.replay(limit=cast(Any, True)))


def test_sqlite_usage_sink_records_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "usage.db"
    sink = SqliteUsageSink(db)
    sink.close()

    assert _metadata_version(db, "usage_sink") == 1


def test_sqlite_usage_sink_totals_include_token_dimensions(tmp_path: Path) -> None:
    db = tmp_path / "usage.db"
    with SqliteUsageSink(db) as sink:
        sink.record(
            "m",
            UsageSnapshot(
                input_tokens=100,
                output_tokens=50,
                cache_write_tokens=10,
                cache_read_tokens=5,
                tool_calls=1,
            ),
            0.005,
        )
        sink.record(
            "m",
            UsageSnapshot(
                input_tokens=200,
                output_tokens=80,
                cache_write_tokens=20,
                cache_read_tokens=15,
                tool_calls=2,
            ),
            0.012,
        )
        totals = sink.totals()

    assert totals == {
        "turns": 2,
        "total_cost_usd": pytest.approx(0.017),
        "total_input_tokens": 300,
        "total_output_tokens": 130,
        "total_cache_write_tokens": 30,
        "total_cache_read_tokens": 20,
        "total_tool_calls": 3,
    }


def test_sqlite_usage_sink_rejects_invalid_records(tmp_path: Path) -> None:
    db = tmp_path / "usage.db"
    with SqliteUsageSink(db) as sink:
        with pytest.raises(ValueError, match="model"):
            sink.record("", UsageSnapshot(input_tokens=100), 0.001)
        with pytest.raises(TypeError, match="usage"):
            sink.record("m", cast(Any, object()), 0.001)
        with pytest.raises(TypeError, match="cost_usd"):
            sink.record("m", UsageSnapshot(input_tokens=100), cast(Any, False))
        with pytest.raises(ValueError, match="cost_usd"):
            sink.record("m", UsageSnapshot(input_tokens=100), -0.001)
        with pytest.raises(ValueError, match="cost_usd"):
            sink.record("m", UsageSnapshot(input_tokens=100), float("nan"))


def test_sqlite_event_and_usage_sinks_can_share_metadata_table(
    tmp_path: Path,
) -> None:
    db = tmp_path / "shared.db"
    event_sink = SqliteEventSink(db)
    usage_sink = SqliteUsageSink(db)
    event_sink.close()
    usage_sink.close()

    assert _metadata_version(db, "event_sink") == 1
    assert _metadata_version(db, "usage_sink") == 1


def test_sqlite_event_sink_rejects_unsupported_schema_version(
    tmp_path: Path,
) -> None:
    db = tmp_path / "future-events.db"
    _write_future_schema_version(db, "event_sink")

    with pytest.raises(RuntimeError, match="unsupported sqlite schema"):
        SqliteEventSink(db)


def test_sqlite_usage_sink_rejects_unsupported_schema_version(
    tmp_path: Path,
) -> None:
    db = tmp_path / "future-usage.db"
    _write_future_schema_version(db, "usage_sink")

    with pytest.raises(RuntimeError, match="unsupported sqlite schema"):
        SqliteUsageSink(db)
