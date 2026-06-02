"""Tests for step-level durability (in-tool-call replay)."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    InMemorySaver,
    SqliteSaver,
    StepCheckpointSaver,
    StepRecord,
)


@pytest.fixture(params=["memory", "sqlite"])
def saver(request, tmp_path):
    if request.param == "memory":
        yield InMemorySaver()
    else:
        s = SqliteSaver(tmp_path / "cp.db")
        yield s
        s.close()


def test_put_then_get_step(saver) -> None:
    rec = saver.put_step("t1", "turn3:search", {"result": "cached"})
    assert isinstance(rec, StepRecord)
    fetched = saver.get_step("t1", "turn3:search")
    assert fetched is not None
    assert fetched.state == {"result": "cached"}


def test_get_missing_step_returns_none(saver) -> None:
    assert saver.get_step("t1", "nope") is None


def test_put_step_overwrites_same_key(saver) -> None:
    saver.put_step("t1", "k", {"v": 1})
    saver.put_step("t1", "k", {"v": 2})
    assert saver.get_step("t1", "k").state == {"v": 2}
    assert len(saver.list_steps("t1")) == 1


def test_list_steps_ordered(saver) -> None:
    saver.put_step("t1", "a", {"n": 1})
    saver.put_step("t1", "b", {"n": 2})
    saver.put_step("t1", "c", {"n": 3})
    keys = [r.step_key for r in saver.list_steps("t1")]
    assert keys == ["a", "b", "c"]


def test_steps_isolated_by_thread(saver) -> None:
    saver.put_step("t1", "k", {"v": 1})
    saver.put_step("t2", "k", {"v": 2})
    assert saver.get_step("t1", "k").state == {"v": 1}
    assert saver.get_step("t2", "k").state == {"v": 2}


def test_delete_clears_steps(saver) -> None:
    saver.put_step("t1", "k", {"v": 1})
    saver.delete("t1")
    assert saver.get_step("t1", "k") is None
    assert saver.list_steps("t1") == []


def test_savers_satisfy_step_protocol(saver) -> None:
    assert isinstance(saver, StepCheckpointSaver)


def test_step_replay_pattern(saver) -> None:
    """The intended use: skip an expensive idempotent step on re-run."""
    calls = {"n": 0}

    def expensive() -> dict:
        calls["n"] += 1
        return {"answer": 42}

    def run() -> dict:
        cached = saver.get_step("t1", "expensive")
        if cached is not None:
            return cached.state
        result = expensive()
        saver.put_step("t1", "expensive", result)
        return result

    assert run() == {"answer": 42}
    assert run() == {"answer": 42}
    assert calls["n"] == 1  # second run hit the cache


def test_sqlite_steps_survive_restart(tmp_path) -> None:
    db = tmp_path / "cp.db"
    s1 = SqliteSaver(db)
    s1.put_step("t1", "k", {"v": "persisted"})
    s1.close()
    s2 = SqliteSaver(db)
    assert s2.get_step("t1", "k").state == {"v": "persisted"}
    s2.close()


def test_non_serializable_state_rejected(saver) -> None:
    with pytest.raises(TypeError):
        saver.put_step("t1", "k", {"bad": object()})
