"""Tests for the remaining Sprint 5 surface:

- ``scheduler.Clock`` / ``SystemClock`` / ``ManualClock``
- ``persistence.SqliteEventSink`` / ``SqliteUsageSink``
- ``PolicyEngine.evaluate_async`` with mixed sync + async conditions
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from techrevati.runtime import (
    AgentEvent,
    Clock,
    ManualClock,
    PhaseContext,
    PolicyAction,
    PolicyActionData,
    PolicyCondition,
    PolicyEngine,
    PolicyRule,
    SqliteEventSink,
    SqliteUsageSink,
    SystemClock,
    UsageSnapshot,
)

# --------------------------------------------------------------------------
# scheduler.Clock — protocol + impls
# --------------------------------------------------------------------------


def test_system_clock_satisfies_protocol() -> None:
    c = SystemClock()
    assert isinstance(c, Clock)
    assert isinstance(c.wall_now(), datetime)
    assert c.monotonic() > 0


def test_manual_clock_satisfies_protocol() -> None:
    c = ManualClock()
    assert isinstance(c, Clock)


def test_manual_clock_rejects_invalid_start_values() -> None:
    with pytest.raises(TypeError, match="start"):
        ManualClock(start=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="start"):
        ManualClock(start=float("nan"))
    with pytest.raises(ValueError, match="start"):
        ManualClock(start=float("inf"))


def test_manual_clock_rejects_naive_wall_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ManualClock(wall_start=datetime(2026, 1, 1))


def test_manual_clock_normalizes_wall_start_to_utc() -> None:
    c = ManualClock(wall_start=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert c.wall_now().tzinfo is UTC


def test_manual_clock_advance_moves_both_clocks_forward() -> None:
    c = ManualClock(start=100.0)
    m0 = c.monotonic()
    w0 = c.wall_now()
    c.advance(60.0)
    assert c.monotonic() == m0 + 60.0
    assert (c.wall_now() - w0).total_seconds() == pytest.approx(60.0)


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf")])
def test_manual_clock_advance_rejects_invalid_durations(seconds: float) -> None:
    c = ManualClock()
    with pytest.raises(ValueError, match="seconds"):
        c.advance(seconds)


def test_manual_clock_advance_rejects_bool_duration() -> None:
    c = ManualClock()
    with pytest.raises(TypeError, match="seconds"):
        c.advance(True)  # type: ignore[arg-type]


def test_manual_clock_tick_to_absolute() -> None:
    c = ManualClock(start=100.0)
    c.tick(250.0)
    assert c.monotonic() == 250.0


def test_manual_clock_tick_cannot_move_backwards() -> None:
    c = ManualClock(start=100.0)
    with pytest.raises(ValueError):
        c.tick(50.0)


@pytest.mark.parametrize("target", [float("nan"), float("inf")])
def test_manual_clock_tick_rejects_non_finite_target(target: float) -> None:
    c = ManualClock()
    with pytest.raises(ValueError, match="absolute_monotonic"):
        c.tick(target)


@pytest.mark.asyncio
async def test_manual_clock_sleep_async_advances_simulated_time() -> None:
    c = ManualClock(start=0.0)
    await c.sleep_async(5.0)
    assert c.monotonic() == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf")])
async def test_clock_sleep_async_rejects_invalid_durations(seconds: float) -> None:
    for clock in (ManualClock(), SystemClock()):
        with pytest.raises(ValueError, match="seconds"):
            await clock.sleep_async(seconds)


@pytest.mark.asyncio
async def test_clock_sleep_async_rejects_bool_duration() -> None:
    for clock in (ManualClock(), SystemClock()):
        with pytest.raises(TypeError, match="seconds"):
            await clock.sleep_async(True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_system_clock_sleep_async_yields() -> None:
    c = SystemClock()
    await c.sleep_async(0)  # zero is a yield-only path
    await c.sleep_async(0.001)


# --------------------------------------------------------------------------
# persistence.SqliteEventSink
# --------------------------------------------------------------------------


def test_sqlite_event_sink_persists_and_replays(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with SqliteEventSink(db) as sink:
        sink.emit(AgentEvent.started("writer", "draft"))
        sink.emit(AgentEvent.completed("writer", "draft", detail="done"))
    # Re-open and replay.
    with SqliteEventSink(db) as sink:
        events = list(sink.replay())
    assert len(events) == 2
    assert events[0].role == "writer"
    assert events[1].detail == "done"


def test_sqlite_event_sink_replay_limit(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    sink = SqliteEventSink(db)
    try:
        for i in range(10):
            sink.emit(AgentEvent.started(f"role-{i}", "phase"))
        assert len(list(sink.replay(limit=3))) == 3
    finally:
        sink.close()


def test_sqlite_event_sink_non_positive_replay_limit_is_empty(
    tmp_path: Path,
) -> None:
    db = tmp_path / "events.db"
    with SqliteEventSink(db) as sink:
        sink.emit(AgentEvent.started("writer", "phase"))
        assert list(sink.replay(limit=0)) == []
        assert list(sink.replay(limit=-1)) == []


# --------------------------------------------------------------------------
# persistence.SqliteUsageSink
# --------------------------------------------------------------------------


def test_sqlite_usage_sink_accumulates_totals(tmp_path: Path) -> None:
    db = tmp_path / "usage.db"
    with SqliteUsageSink(db) as sink:
        sink.record("m", UsageSnapshot(input_tokens=100, output_tokens=50), 0.005)
        sink.record("m", UsageSnapshot(input_tokens=200, output_tokens=80), 0.012)
        totals = sink.totals()
    assert totals["turns"] == 2
    assert totals["total_cost_usd"] == pytest.approx(0.017)


def test_sqlite_usage_sink_normalizes_model_name(tmp_path: Path) -> None:
    db = tmp_path / "usage.db"
    with SqliteUsageSink(db) as sink:
        sink.record(" m ", UsageSnapshot(input_tokens=100), 0.005)
        rows = sink._conn.execute("SELECT model FROM usage_records").fetchall()

    assert rows == [("m",)]


# --------------------------------------------------------------------------
# policy_engine.evaluate_async
# --------------------------------------------------------------------------


class AlwaysSync(PolicyCondition):
    def matches(self, ctx: PhaseContext) -> bool:
        return True


class AsyncTrueAfterYield:
    """Async condition. Implements the matches contract as a coroutine."""

    async def matches(self, ctx: PhaseContext) -> bool:
        import asyncio

        await asyncio.sleep(0)
        return ctx.elapsed_seconds >= 0


class AsyncFalseCondition:
    async def matches(self, ctx: PhaseContext) -> bool:
        import asyncio

        await asyncio.sleep(0)
        return False


@pytest.mark.asyncio
async def test_evaluate_async_runs_sync_and_async_rules_together() -> None:
    action_a = PolicyActionData(action=PolicyAction.NOTIFY, params={"src": "sync"})
    action_b = PolicyActionData(action=PolicyAction.NOTIFY, params={"src": "async"})
    rules = [
        PolicyRule(name="sync", condition=AlwaysSync(), actions=[action_a]),
        PolicyRule(
            name="async-yes",
            condition=AsyncTrueAfterYield(),
            actions=[action_b],
        ),
        PolicyRule(
            name="async-no",
            condition=AsyncFalseCondition(),
            actions=[action_a],
        ),  # skipped
    ]
    engine = PolicyEngine(rules)
    actions = await engine.evaluate_async(PhaseContext(phase="x"))
    assert len(actions) == 2
    assert {a.params.get("src") for a in actions} == {"sync", "async"}
