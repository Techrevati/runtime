"""Tests for techrevati.runtime.checkpoint and orchestrator integration.

The two reference savers (InMemorySaver, SqliteSaver) implement the same
protocol, so most tests are parameterized over both. Sqlite-specific
durability (round-trip across a fresh connection) gets its own test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from techrevati.runtime import (
    AgentSession,
    Checkpoint,
    CheckpointSaver,
    InMemorySaver,
    SqliteSaver,
    UsageSnapshot,
)
from techrevati.runtime.checkpoint import _new_id

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_inmem() -> InMemorySaver:
    return InMemorySaver()


@pytest.fixture
def sqlite_saver(tmp_path: Path) -> Iterator[SqliteSaver]:
    saver = SqliteSaver(tmp_path / "checkpoints.db")
    try:
        yield saver
    finally:
        saver.close()


@pytest.fixture(params=["inmem", "sqlite"])
def saver(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[CheckpointSaver]:
    if request.param == "inmem":
        yield _make_inmem()
        return
    s = SqliteSaver(tmp_path / "param.db")
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_savers_satisfy_protocol(saver: CheckpointSaver) -> None:
    assert isinstance(saver, CheckpointSaver)


# ---------------------------------------------------------------------------
# put / get / list / delete parity
# ---------------------------------------------------------------------------


def test_put_returns_materialized_checkpoint(saver: CheckpointSaver) -> None:
    cp = saver.put("t1", {"k": "v"}, metadata={"step": 1})
    assert cp.thread_id == "t1"
    assert cp.state == {"k": "v"}
    assert cp.metadata == {"step": 1}
    assert cp.id
    assert cp.created_at


def test_get_latest_when_id_is_none(saver: CheckpointSaver) -> None:
    saver.put("t1", {"step": 1})
    second = saver.put("t1", {"step": 2})
    got = saver.get("t1")
    assert got is not None
    assert got.id == second.id
    assert got.state == {"step": 2}


def test_get_by_id_returns_specific(saver: CheckpointSaver) -> None:
    first = saver.put("t1", {"step": 1})
    saver.put("t1", {"step": 2})
    got = saver.get("t1", first.id)
    assert got is not None
    assert got.state == {"step": 1}


def test_get_missing_thread_returns_none(saver: CheckpointSaver) -> None:
    assert saver.get("nope") is None


def test_get_unknown_id_returns_none(saver: CheckpointSaver) -> None:
    saver.put("t1", {"k": "v"})
    assert saver.get("t1", _new_id()) is None


def test_list_orders_newest_first(saver: CheckpointSaver) -> None:
    a = saver.put("t1", {"i": 0})
    b = saver.put("t1", {"i": 1})
    c = saver.put("t1", {"i": 2})
    rows = saver.list("t1")
    assert [r.id for r in rows] == [c.id, b.id, a.id]


def test_list_respects_limit(saver: CheckpointSaver) -> None:
    for i in range(5):
        saver.put("t1", {"i": i})
    rows = saver.list("t1", limit=2)
    assert len(rows) == 2


def test_list_zero_or_negative_limit_returns_empty(saver: CheckpointSaver) -> None:
    saver.put("t1", {"i": 0})
    assert saver.list("t1", limit=0) == []
    assert saver.list("t1", limit=-1) == []


def test_list_before_filters_strictly_earlier(saver: CheckpointSaver) -> None:
    a = saver.put("t1", {"i": 0})
    b = saver.put("t1", {"i": 1})
    c = saver.put("t1", {"i": 2})
    earlier_than_c = saver.list("t1", before=c.id)
    assert [r.id for r in earlier_than_c] == [b.id, a.id]


def test_list_before_unknown_id_returns_empty(saver: CheckpointSaver) -> None:
    saver.put("t1", {"i": 0})
    assert saver.list("t1", before="does-not-exist") == []


def test_delete_removes_thread(saver: CheckpointSaver) -> None:
    saver.put("t1", {"i": 0})
    saver.put("t1", {"i": 1})
    saver.put("t2", {"i": 0})
    saver.delete("t1")
    assert saver.list("t1") == []
    # Other threads untouched.
    assert len(saver.list("t2")) == 1


# ---------------------------------------------------------------------------
# Checkpoint value-object semantics
# ---------------------------------------------------------------------------


def test_checkpoint_to_dict_roundtrip() -> None:
    cp = Checkpoint(
        id="abc",
        thread_id="t1",
        created_at="2026-05-20T00:00:00+00:00",
        state={"k": "v"},
        parent_id="parent",
        metadata={"meta": 1},
    )
    again = Checkpoint.from_dict(cp.to_dict())
    assert again == cp


def test_checkpoint_state_is_copied_on_construction(saver: CheckpointSaver) -> None:
    src = {"k": "v"}
    saver.put("t1", src)
    src["k"] = "mutated"
    # In-memory saver stores the dataclass-frozen copy; mutation of the
    # input dict must NOT alter what comes back from get().
    fetched = saver.get("t1")
    assert fetched is not None
    assert fetched.state == {"k": "v"}


# ---------------------------------------------------------------------------
# SqliteSaver durability
# ---------------------------------------------------------------------------


def test_sqlite_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "durable.db"
    s1 = SqliteSaver(db)
    written = s1.put("t1", {"step": "a"}, metadata={"final": True})
    s1.close()

    s2 = SqliteSaver(db)
    try:
        got = s2.get("t1", written.id)
    finally:
        s2.close()
    assert got is not None
    assert got.state == {"step": "a"}
    assert got.metadata == {"final": True}


def test_sqlite_context_manager_closes() -> None:
    with SqliteSaver(":memory:") as s:
        s.put("t1", {"k": "v"})


# ---------------------------------------------------------------------------
# AgentSession integration: idempotency_key replay
# ---------------------------------------------------------------------------


def test_run_turn_replays_cached_result_on_idempotency_hit() -> None:
    saver = InMemorySaver()
    orch = AgentSession(role="writer", phase="draft", saver=saver)

    call_count = 0

    def expensive() -> str:
        nonlocal call_count
        call_count += 1
        return f"result-{call_count}"

    with orch.session(thread_id="thread-A") as session:
        first, usage1 = session.run_turn(
            expensive,
            model="m",
            usage=UsageSnapshot(input_tokens=1, output_tokens=2),
            idempotency_key="turn-1",
        )

    assert first == "result-1"
    assert call_count == 1

    # Second session with the same thread_id + key must replay.
    with orch.session(thread_id="thread-A") as session:
        second, usage2 = session.run_turn(
            expensive,
            model="m",
            usage=UsageSnapshot(input_tokens=999, output_tokens=999),
            idempotency_key="turn-1",
        )

    assert second == "result-1"
    assert call_count == 1, "fn must NOT be invoked on idempotency replay"
    # Cached usage comes from the checkpoint, not the second call's hint.
    assert usage2.input_tokens == 1


def test_run_turn_without_idempotency_key_does_not_cache_replay() -> None:
    saver = InMemorySaver()
    orch = AgentSession(role="writer", phase="draft", saver=saver)
    call_count = 0

    def fn() -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    with orch.session(thread_id="t1") as session:
        a, _ = session.run_turn(fn)
        b, _ = session.run_turn(fn)
    assert (a, b) == (1, 2)
    # Each turn still writes a checkpoint for restartability.
    assert len(saver.list("t1")) == 2


def test_run_turn_skips_persist_when_no_thread_id() -> None:
    saver = InMemorySaver()
    orch = AgentSession(role="writer", phase="draft", saver=saver)
    with orch.session() as session:
        session.run_turn(lambda: "x", idempotency_key="ignored")
    # No thread_id → no checkpoints written under any thread.
    assert saver.list("default") == []


def test_run_turn_logs_and_skips_non_json_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saver = InMemorySaver()
    orch = AgentSession(role="writer", phase="draft", saver=saver)

    class NotJsonable:
        def __repr__(self) -> str:
            return "NotJsonable()"

    with orch.session(thread_id="t1") as session:
        with caplog.at_level("WARNING", logger="techrevati.runtime.orchestrator"):
            result, _ = session.run_turn(
                lambda: NotJsonable(),
                idempotency_key="key-1",
            )
    assert isinstance(result, NotJsonable)
    assert saver.list("t1") == []  # Skipped because of non-serializable result.
    assert any("not JSON-serializable" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_arun_turn_replays_on_idempotency_hit() -> None:
    saver = InMemorySaver()
    orch = AgentSession(role="writer", phase="draft", saver=saver)

    call_count = 0

    async def expensive() -> str:
        nonlocal call_count
        call_count += 1
        return f"async-{call_count}"

    async with orch.asession(thread_id="async-thread") as session:
        first, _ = await session.arun_turn(
            expensive,
            usage=UsageSnapshot(input_tokens=10, output_tokens=5),
            idempotency_key="async-1",
        )
    assert first == "async-1"

    async with orch.asession(thread_id="async-thread") as session:
        second, _ = await session.arun_turn(
            expensive,
            usage=UsageSnapshot(input_tokens=999, output_tokens=999),
            idempotency_key="async-1",
        )
    assert second == "async-1"
    assert call_count == 1


# ---------------------------------------------------------------------------
# AgentSession integration: restart-resumable across processes
# ---------------------------------------------------------------------------


def test_sqlite_backed_session_resumes_across_saver_reopen(tmp_path: Path) -> None:
    db = tmp_path / "resume.db"

    # First "process": run turn-1 only.
    saver1 = SqliteSaver(db)
    orch1 = AgentSession(role="writer", phase="draft", saver=saver1)
    with orch1.session(thread_id="resumable") as session:
        session.run_turn(lambda: "turn-1", idempotency_key="t1")
    saver1.close()

    # Second "process": same thread_id picks up turn-1's checkpoint, runs turn-2.
    saver2 = SqliteSaver(db)
    orch2 = AgentSession(role="writer", phase="draft", saver=saver2)
    invocations = 0

    def turn_1_replacement() -> str:
        nonlocal invocations
        invocations += 1
        return "should-not-run"

    with orch2.session(thread_id="resumable") as session:
        replayed, _ = session.run_turn(turn_1_replacement, idempotency_key="t1")
        session.run_turn(lambda: "turn-2", idempotency_key="t2")
    saver2.close()

    assert replayed == "turn-1"
    assert invocations == 0, "turn-1 must replay from the checkpoint"

    # Verify both turns are durably stored.
    saver3 = SqliteSaver(db)
    try:
        rows = saver3.list("resumable")
    finally:
        saver3.close()
    assert len(rows) == 2
    keys = sorted(r.metadata.get("idempotency_key") for r in rows)
    assert keys == ["t1", "t2"]


# ---------------------------------------------------------------------------
# Type-narrowing smoke: the saver field on AgentSession accepts the protocol
# ---------------------------------------------------------------------------


def test_orchestrator_saver_field_accepts_protocol_subtypes() -> None:
    saver: CheckpointSaver = InMemorySaver()
    orch = AgentSession(role="r", phase="p", saver=saver)
    # Round-trip through the dataclass field.
    assert cast(CheckpointSaver, orch.saver) is saver
