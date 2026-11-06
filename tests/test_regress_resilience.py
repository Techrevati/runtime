"""Regressions for the resilience review: SQLite recovery, breaker probes, limits."""

from __future__ import annotations

import asyncio
import copy
import json
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from techrevati.runtime import (
    AgentEvent,
    AsyncCircuitBreaker,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    FailureScenario,
    InMemorySaver,
    ManualClock,
    SqliteEventSink,
    SqliteSaver,
    SqliteUsageSink,
    StaticProviderRouter,
    TokenBucket,
    UsageSnapshot,
    classify_exception,
)
from techrevati.runtime.rate_limit import (
    AsyncRateLimiter,
    AsyncTokenBucket,
    RateLimiter,
    RateLimitExceededError,
)
from techrevati.runtime.routing import RoundRobinProviderRouter, WeightedProviderRouter

# -- SQLite: a failed write must not wedge the connection ---------------------


def _collide_then_recover(path: Path, conn: Any, write: Any) -> None:
    """Fail one write on a lock, pin nothing, then let another writer commit."""
    conn.execute("PRAGMA busy_timeout=20")
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        write()
    holder.execute("ROLLBACK")
    holder.close()
    assert conn.in_transaction is False


def test_saver_recovers_after_lock_timeout(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    saver = SqliteSaver(path)
    other = SqliteSaver(path)
    try:
        saver.put("t", {"n": 0})
        _collide_then_recover(path, saver._conn, lambda: saver.put("t", {"n": 1}))
        saver.get("t")
        other.put("t", {"n": 99})
        latest = saver.get("t")
        assert latest is not None and latest.state == {"n": 99}
        saver.put("t", {"n": 100})
        latest = other.get("t")
        assert latest is not None and latest.state == {"n": 100}
    finally:
        saver.close()
        other.close()


@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.put_step("t", "k", {"v": 1}),
        lambda s: s.delete("t"),
    ],
    ids=["put_step", "delete"],
)
def test_saver_other_writes_recover_after_lock_timeout(
    tmp_path: Path, operation: Any
) -> None:
    path = tmp_path / "c.db"
    saver = SqliteSaver(path)
    try:
        _collide_then_recover(path, saver._conn, lambda: operation(saver))
        operation(saver)
    finally:
        saver.close()


def test_event_sink_recovers_after_lock_timeout(tmp_path: Path) -> None:
    path = tmp_path / "e.db"
    event = AgentEvent.started("r", "p")
    with SqliteEventSink(path) as sink, SqliteEventSink(path) as other:
        _collide_then_recover(path, sink._conn, lambda: sink.emit(event))
        list(sink.replay())
        other.emit(event)
        sink.emit(event)
        assert len(list(sink.replay())) == 2


def test_usage_sink_recovers_after_lock_timeout(tmp_path: Path) -> None:
    path = tmp_path / "u.db"
    usage = UsageSnapshot(input_tokens=1, output_tokens=1)
    with SqliteUsageSink(path) as sink, SqliteUsageSink(path) as other:
        _collide_then_recover(path, sink._conn, lambda: sink.record("m", usage, 1.0))
        sink.totals()
        other.record("m", usage, 1.0)
        assert sink.totals()["turns"] == 1
        sink.record("m", usage, 1.0)
        assert other.totals()["turns"] == 2


# -- SQLite: concurrent cold start --------------------------------------------


def test_concurrent_cold_start_does_not_fail(tmp_path: Path) -> None:
    workers = 8
    errors: list[BaseException] = []
    for round_number in range(10):
        path = tmp_path / f"cold-{round_number}.db"
        barrier = threading.Barrier(workers)

        def open_saver(target: Path = path, gate: Any = barrier) -> None:
            gate.wait()
            try:
                SqliteSaver(target).close()
            except Exception as exc:
                errors.append(exc)

        with ThreadPoolExecutor(workers) as pool:
            for future in [pool.submit(open_saver) for _ in range(workers)]:
                future.result()
    assert errors == []


def test_schema_version_mismatch_still_rejected(tmp_path: Path) -> None:
    path = tmp_path / "v.db"
    SqliteSaver(path).close()
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("UPDATE techrevati_runtime_metadata SET schema_version = 99")
    conn.close()
    with pytest.raises(RuntimeError, match="unsupported sqlite schema"):
        SqliteSaver(path)


def test_open_wal_retries_lock_contention(tmp_path: Path) -> None:
    from techrevati.runtime._internal import _open_wal

    class _Connection:
        attempts = 0

        def execute(self, sql: str) -> None:
            _Connection.attempts += 1
            if _Connection.attempts < 3:
                raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            raise AssertionError("must not close on a successful retry")

    with (
        patch("techrevati.runtime._internal.sqlite3.connect") as connect,
        patch("techrevati.runtime._internal.time.sleep"),
    ):
        connect.return_value = _Connection()
        _open_wal(tmp_path / "x.db")
    assert _Connection.attempts == 3


# -- SQLite: JSON-valid state that UTF-8 cannot bind --------------------------


def test_sqlite_saver_stores_lone_surrogates_like_in_memory(tmp_path: Path) -> None:
    state = json.loads('{"result": "cut \\ud83d"}')
    with SqliteSaver(tmp_path / "c.db") as saver:
        saver.put("t", state, metadata=state)
        saver.put_step("t", "k", state)
        latest = saver.get("t")
        step = saver.get_step("t", "k")
    assert latest is not None and latest.state == state and latest.metadata == state
    assert step is not None and step.state == state
    assert InMemorySaver().put("t", state).state == state


def test_sqlite_saver_reads_rows_written_with_raw_unicode(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    SqliteSaver(path).close()
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO checkpoints"
            " (id, thread_id, created_at, state_json, parent_id, metadata_json)"
            " VALUES ('old', 't', '2026-01-01', ?, NULL, '{}')",
            (json.dumps({"text": "šđč 🙂"}, ensure_ascii=False),),
        )
    conn.close()
    with SqliteSaver(path) as saver:
        latest = saver.get("t")
        assert latest is not None and latest.state == {"text": "šđč 🙂"}


def test_sinks_store_lone_surrogates(tmp_path: Path) -> None:
    with SqliteEventSink(tmp_path / "e.db") as sink:
        sink.emit(AgentEvent.completed("r\ud83d", "p", detail="cut \ud83d"))
        replayed = list(sink.replay())
    assert replayed[0].detail == "cut \ud83d"
    assert replayed[0].role == "r\ud83d"
    with SqliteUsageSink(tmp_path / "u.db") as usage_sink:
        usage_sink.record("m\ud83d", UsageSnapshot(input_tokens=1), 0.5)
        assert usage_sink.totals()["turns"] == 1


# -- Checkpoint ordering and aliasing -----------------------------------------


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_latest_checkpoint_survives_backward_clock_step(
    tmp_path: Path, backend: str
) -> None:
    saver: Any = InMemorySaver() if backend == "memory" else SqliteSaver(tmp_path / "c")
    with patch("techrevati.runtime.checkpoint._now_iso") as now:
        now.side_effect = ["2026-05-30T00:00:02+00:00", "2026-05-30T00:00:01+00:00"]
        saver.put("t", {"turn": 1})
        second = saver.put("t", {"turn": 2})
    latest = saver.get("t")
    assert latest.state == {"turn": 2}
    listed = saver.list("t")
    assert [cp.state["turn"] for cp in listed] == [2, 1]
    assert [cp.state["turn"] for cp in saver.list("t", before=second.id)] == [1]


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_list_limit_keeps_newest(tmp_path: Path, backend: str) -> None:
    saver: Any = InMemorySaver() if backend == "memory" else SqliteSaver(tmp_path / "c")
    for turn in range(5):
        saver.put("t", {"turn": turn})
    assert [cp.state["turn"] for cp in saver.list("t", limit=2)] == [4, 3]


def test_in_memory_step_records_do_not_alias_storage() -> None:
    saver = InMemorySaver()
    saver.put_step("t", "k", {"v": {"a": 1}}).state["v"]["a"] = "put"
    first = saver.get_step("t", "k")
    assert first is not None
    first.state["x"] = "get"
    saver.list_steps("t")[0].state["y"] = "list"
    stored = saver.get_step("t", "k")
    assert stored is not None and stored.state == {"v": {"a": 1}}


# -- Routing ------------------------------------------------------------------


@pytest.mark.parametrize(
    "router",
    [
        StaticProviderRouter(("a", "b", "c")),
        RoundRobinProviderRouter(("a", "b", "c")),
        WeightedProviderRouter((("a", 3.0), ("b", 2.0), ("c", 1.0))),
    ],
    ids=["static", "round_robin", "weighted"],
)
def test_router_tolerates_repeated_exclude(router: Any) -> None:
    selected = router.select(
        scenario=FailureScenario.PROVIDER_FAILURE,
        attempt=1,
        current="a",
        exclude=["b", "b"],
    )
    assert selected == "c"


def test_router_still_returns_none_for_invalid_exclude() -> None:
    router = StaticProviderRouter(("a", "b"))
    for bad in ("b", ["", "b"], [1]):
        selected = router.select(
            scenario=FailureScenario.PROVIDER_FAILURE,
            attempt=1,
            current="a",
            exclude=bad,  # type: ignore[arg-type]
        )
        assert selected is None


# -- Classification -----------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "This model's maximum context length is 8192 tokens. However, your "
            "messages resulted in 14290 tokens.",
            FailureScenario.CONTEXT_OVERFLOW,
        ),
        (
            "context_length_exceeded: request has 42901 tokens",
            FailureScenario.CONTEXT_OVERFLOW,
        ),
        ("backend returned 500 for job 15032", FailureScenario.LLM_ERROR),
        ("failed: request_id=req_84010", FailureScenario.LLM_ERROR),
        ("HTTP 429", FailureScenario.LLM_ERROR),
        ("status_code=503", FailureScenario.PROVIDER_FAILURE),
        ("error 502 from gateway", FailureScenario.PROVIDER_FAILURE),
        ("got 401", FailureScenario.PROVIDER_FAILURE),
        (
            "rate limit: too many tokens per minute",
            FailureScenario.LLM_ERROR,
        ),
        ("429: too many tokens per minute", FailureScenario.LLM_ERROR),
        ("too many tokens in prompt", FailureScenario.CONTEXT_OVERFLOW),
    ],
)
def test_classifier_matches_whole_status_codes(
    message: str, expected: FailureScenario
) -> None:
    assert classify_exception(Exception(message)) == expected


# -- Circuit breaker ----------------------------------------------------------


def _opened(clock: ManualClock, **kwargs: Any) -> CircuitBreaker:
    breaker = CircuitBreaker(
        "svc",
        failure_threshold=1,
        recovery_timeout_seconds=10.0,
        clock=clock,
        **kwargs,
    )
    breaker.record_failure()
    clock.advance(10.0)
    return breaker


def _call_blocked(breaker: CircuitBreaker, *, succeed: bool = True) -> Any:
    """Start a probe on a thread and return (entered, release, future)."""
    entered = threading.Event()
    release = threading.Event()

    def probe() -> str:
        entered.set()
        assert release.wait(5)
        if not succeed:
            raise RuntimeError("probe failed")
        return "ok"

    pool = ThreadPoolExecutor(1)
    future = pool.submit(breaker.call, probe)
    pool.shutdown(wait=False)
    assert entered.wait(5)
    return release, future


def test_hung_probe_is_reclaimed_after_recovery_window() -> None:
    clock = ManualClock()
    breaker = _opened(clock)
    release, future = _call_blocked(breaker)

    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "blocked")
    clock.advance(10.0)
    assert breaker.call(lambda: "reclaimed") == "reclaimed"
    assert breaker.state() == CircuitState.CLOSED

    release.set()
    future.result(5)
    assert breaker.state() == CircuitState.CLOSED


def test_recovery_timeout_zero_keeps_probe_limit() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=0.0, clock=clock
    )
    breaker.record_failure()
    release, future = _call_blocked(breaker)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "blocked")
    release.set()
    future.result(5)


def test_stale_probe_success_does_not_close_current_cycle() -> None:
    clock = ManualClock()
    breaker = _opened(clock, half_open_max_probes=2)
    release_failing, failing = _call_blocked(breaker, succeed=False)
    release_stale, stale = _call_blocked(breaker)

    release_failing.set()
    with pytest.raises(RuntimeError):
        failing.result(5)
    assert breaker._state == CircuitState.OPEN

    clock.advance(10.0)
    release_current, current = _call_blocked(breaker, succeed=False)
    release_stale.set()
    stale.result(5)
    assert breaker._state == CircuitState.HALF_OPEN
    assert breaker._probe_in_flight == 1

    release_current.set()
    with pytest.raises(RuntimeError):
        current.result(5)
    assert breaker._state == CircuitState.OPEN


def test_stale_probe_failure_does_not_reopen_later_cycle() -> None:
    clock = ManualClock()
    breaker = _opened(clock)
    release, hung = _call_blocked(breaker, succeed=False)
    clock.advance(10.0)
    assert breaker.call(lambda: "reclaimed") == "reclaimed"

    release.set()
    with pytest.raises(RuntimeError):
        hung.result(5)
    assert breaker.state() == CircuitState.CLOSED
    assert breaker._failure_count == 0


def test_record_success_closes_when_state_reports_half_open() -> None:
    clock = ManualClock()
    breaker = _opened(clock)
    assert breaker.state() == CircuitState.HALF_OPEN
    breaker.record_success()
    assert breaker.state() == CircuitState.CLOSED


def test_record_success_does_not_close_before_recovery_window() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock
    )
    breaker.record_failure()
    breaker.record_success()
    assert breaker.state() == CircuitState.OPEN


def test_half_open_stops_admitting_once_all_probes_issued() -> None:
    clock = ManualClock()
    breaker = _opened(clock, half_open_max_probes=2)
    release_a, a = _call_blocked(breaker)
    release_b, b = _call_blocked(breaker)

    release_a.set()
    a.result(5)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "must not refill the freed slot")

    release_b.set()
    b.result(5)
    assert breaker.state() == CircuitState.CLOSED


def test_half_open_closes_under_sustained_concurrent_load() -> None:
    clock = ManualClock()
    breaker = _opened(clock, half_open_max_probes=8)
    stop = threading.Event()
    jitter = random.Random(7)

    def probe() -> None:
        time.sleep(jitter.uniform(0.005, 0.05))

    def hammer() -> None:
        while not stop.is_set():
            try:
                breaker.call(probe)
            except CircuitOpenError:
                time.sleep(0.0005)

    workers = 32
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(hammer) for _ in range(workers)]
        deadline = time.monotonic() + 2.0
        closed = False
        while time.monotonic() < deadline and not closed:
            closed = breaker.state() == CircuitState.CLOSED
            time.sleep(0.005)
        stop.set()
        for future in futures:
            future.result(5)
    assert closed


def test_cancelled_probe_frees_its_issued_slot() -> None:
    clock = ManualClock()
    breaker = _opened(clock)

    def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        breaker.call(interrupted)
    assert breaker.call(lambda: "next") == "next"
    assert breaker.state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_async_breaker_reclaims_hung_probe_and_ignores_its_result() -> None:
    clock = ManualClock()
    breaker = AsyncCircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock
    )
    await breaker.record_failure()
    clock.advance(10.0)

    release = asyncio.Event()

    async def hung() -> str:
        await release.wait()
        raise RuntimeError("late failure")

    async def fine() -> str:
        return "ok"

    task = asyncio.create_task(breaker.call(hung))
    await asyncio.sleep(0)
    with pytest.raises(CircuitOpenError):
        await breaker.call(fine)
    clock.advance(10.0)
    assert await breaker.call(fine) == "ok"

    release.set()
    with pytest.raises(RuntimeError):
        await task
    assert await breaker.state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_async_record_success_closes_virtual_half_open() -> None:
    clock = ManualClock()
    breaker = AsyncCircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock
    )
    await breaker.record_failure()
    clock.advance(10.0)
    await breaker.record_success()
    assert await breaker.state() == CircuitState.CLOSED


@pytest.mark.parametrize("breaker_type", [CircuitBreaker, AsyncCircuitBreaker])
def test_breaker_rejects_nan_recovery_timeout(breaker_type: Any) -> None:
    with pytest.raises(ValueError, match="recovery_timeout_seconds"):
        breaker_type("svc", recovery_timeout_seconds=float("nan"))


# -- Rate limiting ------------------------------------------------------------


def _advance_on_sleep(clock: ManualClock) -> Any:
    def sleeper(seconds: float) -> None:
        clock.advance(seconds)

    return patch("techrevati.runtime.rate_limit.time.sleep", sleeper)


def test_acquire_usage_above_capacity_debits_instead_of_refusing() -> None:
    clock = ManualClock()
    bucket = TokenBucket("input_tpm", 100.0, 10.0, clock=clock)
    limiter = RateLimiter({"input_tpm": bucket})
    with _advance_on_sleep(clock):
        limiter.acquire_usage(input_tokens=200, output_tokens=0)
    assert bucket.available == pytest.approx(0.0, abs=1e-6)
    assert clock() == pytest.approx(1010.0)


def test_acquire_usage_timeout_keeps_all_debits() -> None:
    clock = ManualClock()
    in_b = TokenBucket("input_tpm", 10.0, 1.0, clock=clock)
    out_b = TokenBucket("output_tpm", 10.0, 1.0, clock=clock)
    limiter = RateLimiter({"input_tpm": in_b, "output_tpm": out_b})
    with pytest.raises(RateLimitExceededError) as caught:
        limiter.acquire_usage(input_tokens=4, output_tokens=25, timeout=0.0)
    assert caught.value.bucket_name == "output_tpm"
    assert in_b.available == pytest.approx(6.0)
    assert out_b.available == pytest.approx(-15.0)


def test_acquire_usage_timeout_is_shared_across_buckets() -> None:
    clock = ManualClock()
    in_b = TokenBucket("input_tpm", 10.0, 1.0, clock=clock)
    out_b = TokenBucket("output_tpm", 10.0, 1.0, clock=clock)
    assert in_b.try_acquire(10.0) and out_b.try_acquire(10.0)
    limiter = RateLimiter({"input_tpm": in_b, "output_tpm": out_b})
    start = clock()
    with _advance_on_sleep(clock), pytest.raises(RateLimitExceededError):
        limiter.acquire_usage(input_tokens=1, output_tokens=2, timeout=1.5)
    assert clock() - start <= 1.5


def test_acquire_after_debt_waits_for_repayment() -> None:
    clock = ManualClock()
    bucket = TokenBucket("b", 10.0, 1.0, clock=clock)
    bucket.debit(15.0)
    assert bucket.available == pytest.approx(-5.0)
    assert bucket.try_acquire(1.0) is False
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(1.0, timeout=5.0)
    clock.advance(6.0)
    assert bucket.try_acquire(1.0) is True


def test_acquire_for_more_than_capacity_still_refuses() -> None:
    bucket = TokenBucket("b", 10.0, 1.0, clock=ManualClock())
    with pytest.raises(RateLimitExceededError):
        bucket.acquire(11.0)
    assert bucket.available == pytest.approx(10.0)


def test_debit_rejects_invalid_amount() -> None:
    bucket = TokenBucket("b", 10.0, 1.0, clock=ManualClock())
    with pytest.raises(ValueError, match="tokens"):
        bucket.debit(-1.0)


@pytest.mark.asyncio
async def test_async_acquire_usage_debits_and_keeps_debits_on_timeout() -> None:
    clock = ManualClock()
    in_b = AsyncTokenBucket("input_tpm", 10.0, 1.0, clock=clock)
    out_b = AsyncTokenBucket("output_tpm", 10.0, 1.0, clock=clock)
    limiter = AsyncRateLimiter({"input_tpm": in_b, "output_tpm": out_b})
    with pytest.raises(RateLimitExceededError):
        await limiter.acquire_usage(input_tokens=4, output_tokens=25, timeout=0.0)
    assert await in_b.available() == pytest.approx(6.0)
    assert await out_b.available() == pytest.approx(-15.0)


@pytest.mark.asyncio
async def test_async_acquire_usage_above_capacity_settles() -> None:
    clock = ManualClock()
    bucket = AsyncTokenBucket("input_tpm", 100.0, 1000.0, clock=clock)
    limiter = AsyncRateLimiter({"input_tpm": bucket})

    async def fake_sleep(seconds: float) -> None:
        clock.advance(seconds)

    with patch("techrevati.runtime.rate_limit.asyncio.sleep", fake_sleep):
        await limiter.acquire_usage(input_tokens=200, output_tokens=0)
    assert await bucket.available() == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("bucket_type", [TokenBucket, AsyncTokenBucket])
def test_bucket_accepts_negative_clock_epoch(bucket_type: Any) -> None:
    bucket_type("b", 1.0, 1.0, clock=ManualClock(start=-5.0))


@pytest.mark.parametrize(
    ("bad", "error"),
    [(float("nan"), ValueError), (float("inf"), ValueError), (True, TypeError)],
)
def test_bucket_rejects_unusable_clock_values(bad: Any, error: type[Exception]) -> None:
    with pytest.raises(error, match="clock"):
        TokenBucket("b", 1.0, 1.0, clock=lambda: bad)


# -- Exceptions and clocks ----------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [RateLimitExceededError("input_tpm", 5.0), CircuitOpenError("svc")],
    ids=["rate_limit", "circuit_open"],
)
def test_exceptions_survive_pickle_and_copy(error: Exception) -> None:
    # copy.copy rebuilds through __reduce_ex__, the same path pickle takes.
    clone = copy.copy(error)
    assert type(clone) is type(error)
    assert str(clone) == str(error)
    assert vars(clone) == vars(error)


def test_manual_clock_overflow_leaves_both_clocks_unchanged() -> None:
    clock = ManualClock()
    wall = clock.wall_now()
    with pytest.raises(OverflowError):
        clock.advance(1e300)
    assert clock.monotonic() == 1000.0
    assert clock.wall_now() == wall
    with pytest.raises(OverflowError):
        clock.tick(1e300)
    assert clock.monotonic() == 1000.0


# -- Adversarial review of the fixes above ------------------------------------


def _trip_with_slow_call_in_flight(clock: ManualClock) -> Any:
    breaker = CircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock
    )
    release, slow = _call_blocked(breaker, succeed=False)
    breaker.record_failure()
    assert breaker._state == CircuitState.OPEN
    return breaker, release, slow


def test_late_closed_era_failure_does_not_tear_down_probe_cycle() -> None:
    clock = ManualClock()
    breaker, release_slow, slow = _trip_with_slow_call_in_flight(clock)
    tripped_at = breaker._last_failure_time
    clock.advance(10.0)
    release_probe, probe = _call_blocked(breaker)

    clock.advance(1.0)
    release_slow.set()
    with pytest.raises(RuntimeError):
        slow.result(5)
    assert breaker._state == CircuitState.HALF_OPEN
    assert breaker._last_failure_time == tripped_at

    release_probe.set()
    probe.result(5)
    assert breaker.state() == CircuitState.CLOSED


def test_late_closed_era_failure_does_not_delay_recovery_while_open() -> None:
    clock = ManualClock()
    breaker, release_slow, slow = _trip_with_slow_call_in_flight(clock)
    clock.advance(9.0)
    release_slow.set()
    with pytest.raises(RuntimeError):
        slow.result(5)
    clock.advance(1.0)
    assert breaker.call(lambda: "probe") == "probe"
    assert breaker.state() == CircuitState.CLOSED


def test_same_generation_closed_failures_still_trip() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(
        "svc", failure_threshold=2, recovery_timeout_seconds=10.0, clock=clock
    )
    release_a, a = _call_blocked(breaker, succeed=False)
    release_b, b = _call_blocked(breaker, succeed=False)
    release_a.set()
    release_b.set()
    for future in (a, b):
        with pytest.raises(RuntimeError):
            future.result(5)
    assert breaker._state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_async_late_closed_era_failure_does_not_tear_down_probe_cycle() -> None:
    clock = ManualClock()
    breaker = AsyncCircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock
    )
    release_slow = asyncio.Event()
    release_probe = asyncio.Event()

    async def slow_failure() -> None:
        await release_slow.wait()
        raise RuntimeError("late")

    async def probe() -> str:
        await release_probe.wait()
        return "ok"

    slow = asyncio.create_task(breaker.call(slow_failure))
    await asyncio.sleep(0)
    await breaker.record_failure()
    clock.advance(10.0)
    probing = asyncio.create_task(breaker.call(probe))
    await asyncio.sleep(0)

    release_slow.set()
    with pytest.raises(RuntimeError):
        await slow
    assert breaker._state == CircuitState.HALF_OPEN

    release_probe.set()
    assert await probing == "ok"
    assert await breaker.state() == CircuitState.CLOSED


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "tool 'search' failed (request_id=7a503bc3-1111-4222-8333-abcdefabcdef)",
            FailureScenario.TOOL_EXECUTION_ERROR,
        ),
        (
            "tool 'search' failed (request_id=1111-503-8333)",
            FailureScenario.TOOL_EXECUTION_ERROR,
        ),
        (
            "tool 'search' failed (request_id=7a401bc3)",
            FailureScenario.TOOL_EXECUTION_ERROR,
        ),
        ("Tool failed: took 401ms", FailureScenario.TOOL_EXECUTION_ERROR),
        ("req_a401b: bad argument", FailureScenario.LLM_ERROR),
        ("version 1.503 rejected", FailureScenario.LLM_ERROR),
        (
            "ThrottlingException: Too many tokens, please wait before trying again.",
            FailureScenario.LLM_ERROR,
        ),
        ("Too many tokens, please wait", FailureScenario.LLM_ERROR),
        ("rate_limit_error: slow down", FailureScenario.LLM_ERROR),
        (
            "prompt is too long: 210000 tokens > 200000 maximum",
            FailureScenario.CONTEXT_OVERFLOW,
        ),
        ("Input is too long for requested model.", FailureScenario.CONTEXT_OVERFLOW),
    ],
)
def test_classifier_rejects_status_lookalikes(
    message: str, expected: FailureScenario
) -> None:
    assert classify_exception(Exception(message)) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("status 503", FailureScenario.PROVIDER_FAILURE),
        ("HTTP/1.1 503", FailureScenario.PROVIDER_FAILURE),
        ("server returned 503.", FailureScenario.PROVIDER_FAILURE),
        ("(503)", FailureScenario.PROVIDER_FAILURE),
        ("Upstream connect error 503", FailureScenario.PROVIDER_FAILURE),
        ("502: try again later", FailureScenario.PROVIDER_FAILURE),
        (
            "503 Service Unavailable. Please try again later.",
            FailureScenario.PROVIDER_FAILURE,
        ),
        ("error code: 401", FailureScenario.PROVIDER_FAILURE),
        ("Error code: 401 - invalid key", FailureScenario.PROVIDER_FAILURE),
        ("HTTP 429", FailureScenario.LLM_ERROR),
        ("429 Too Many Requests", FailureScenario.LLM_ERROR),
        ("Error code: 429 - {'error': 'x'}", FailureScenario.LLM_ERROR),
        ("too many tokens", FailureScenario.CONTEXT_OVERFLOW),
    ],
)
def test_classifier_keeps_realistic_http_phrasing(
    message: str, expected: FailureScenario
) -> None:
    assert classify_exception(Exception(message)) == expected


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), 1e308, 10**400])
def test_debit_rejects_absurd_amounts(bad: Any) -> None:
    bucket = TokenBucket("b", 10.0, 1.0, clock=ManualClock())
    with pytest.raises(ValueError, match="tokens"):
        bucket.debit(bad)
    assert bucket.available == pytest.approx(10.0)


def test_acquire_usage_rejects_absurd_amount_before_any_debit() -> None:
    clock = ManualClock()
    in_b = TokenBucket("input_tpm", 10.0, 1.0, clock=clock)
    limiter = RateLimiter({"input_tpm": in_b})
    with pytest.raises(ValueError, match="output_tokens"):
        limiter.acquire_usage(input_tokens=5, output_tokens=10**400)
    assert in_b.available == pytest.approx(10.0)


@pytest.mark.parametrize("value", [10**400, "1" * 400])
def test_amount_validation_does_not_leak_overflow(value: Any) -> None:
    bucket = TokenBucket("b", 10.0, 1.0, clock=ManualClock())
    with pytest.raises(ValueError, match="tokens"):
        bucket.try_acquire(value)


def test_debt_is_capped_and_stays_repayable() -> None:
    clock = ManualClock()
    bucket = TokenBucket("b", 10.0, 1.0, clock=clock)
    for _ in range(100):
        bucket.debit(1e15)
    assert bucket.available == pytest.approx(-86_400.0)
    clock.advance(0.05)
    assert bucket.available == pytest.approx(-86_399.95)
    clock.advance(86_401.0)
    assert bucket.try_acquire(1.0) is True


@pytest.mark.asyncio
async def test_async_debt_is_capped() -> None:
    clock = ManualClock()
    bucket = AsyncTokenBucket("b", 10.0, 2.0, clock=clock)
    await bucket.debit(1e15)
    assert await bucket.available() == pytest.approx(-172_800.0)
    with pytest.raises(ValueError, match="tokens"):
        await bucket.debit(1e308)


def test_event_sink_stores_surrogate_timestamp(tmp_path: Path) -> None:
    event = AgentEvent.started("r", "p")
    broken = AgentEvent.from_dict({**event.to_dict(), "emitted_at": "\ud800"})
    with SqliteEventSink(tmp_path / "e.db") as sink:
        sink.emit(broken)
        replayed = list(sink.replay())
    assert replayed[0].emitted_at == "\ud800"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize(
    "operation",
    [
        lambda s: s.put("a\ud800", {}),
        lambda s: s.get("a\ud800"),
        lambda s: s.get("t", "a\ud800"),
        lambda s: s.list("a\ud800"),
        lambda s: s.list("t", before="a\ud800"),
        lambda s: s.delete("a\ud800"),
        lambda s: s.put("t", {}, parent_id="a\ud800"),
        lambda s: s.put_step("a\ud800", "k", {}),
        lambda s: s.put_step("t", "k\ud800", {}),
        lambda s: s.get_step("t", "k\ud800"),
        lambda s: s.list_steps("a\ud800"),
    ],
)
def test_savers_reject_surrogate_keys_alike(
    tmp_path: Path, backend: str, operation: Any
) -> None:
    saver: Any = InMemorySaver() if backend == "memory" else SqliteSaver(tmp_path / "c")
    with pytest.raises(ValueError, match="valid Unicode"):
        operation(saver)


def test_saver_keeps_literal_escape_text_as_its_own_thread(tmp_path: Path) -> None:
    with SqliteSaver(tmp_path / "c") as saver:
        saver.put("a\\ud800", {"n": 1})
        latest = saver.get("a\\ud800")
    assert latest is not None and latest.state == {"n": 1}


# -- Slow but healthy downstream ----------------------------------------------


def test_slow_healthy_downstream_closes_under_sustained_load() -> None:
    clock = ManualClock()
    breaker = CircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=1.0, clock=clock
    )
    breaker.record_failure()
    clock.advance(1.0)
    in_flight: list[tuple[float, Any]] = []
    peak = 0
    for _ in range(200):  # a new caller every 0.25 s; each call succeeds after 2 s
        clock.advance(0.25)
        for entry in [e for e in in_flight if clock() >= e[0]]:
            with breaker._lock:
                breaker._record_call_success_locked(entry[1])
            in_flight.remove(entry)
        if breaker.state() == CircuitState.CLOSED:
            break
        try:
            in_flight.append((clock() + 2.0, _admit(breaker)))
        except CircuitOpenError:
            continue
        peak = max(peak, len(in_flight))
    assert breaker.state() == CircuitState.CLOSED
    assert peak <= 3


def _admit(breaker: CircuitBreaker) -> Any:
    with breaker._lock:
        return breaker._admit_locked()


def test_abandoned_probe_success_closes_but_its_failure_does_not_count() -> None:
    clock = ManualClock()
    breaker = _opened(clock)
    abandoned = _admit(breaker)
    clock.advance(10.0)
    current = _admit(breaker)

    with breaker._lock:
        breaker._record_call_failure_locked(abandoned)
    assert breaker._state == CircuitState.HALF_OPEN
    assert breaker._probe_in_flight == 1

    with breaker._lock:
        breaker._record_call_success_locked(abandoned)
    assert breaker._state == CircuitState.CLOSED
    assert current.is_probe


def test_success_admitted_before_a_later_failure_stays_ignored() -> None:
    clock = ManualClock()
    breaker = _opened(clock, half_open_max_probes=2)
    earlier = _admit(breaker)
    failing = _admit(breaker)
    with breaker._lock:
        breaker._record_call_failure_locked(failing)
    clock.advance(10.0)
    _admit(breaker)
    with breaker._lock:
        breaker._record_call_success_locked(earlier)
    assert breaker._state == CircuitState.HALF_OPEN


def test_released_abandoned_probe_does_not_free_the_current_slot() -> None:
    clock = ManualClock()
    breaker = _opened(clock)
    abandoned = _admit(breaker)
    clock.advance(10.0)
    _admit(breaker)
    with breaker._lock:
        breaker._release_probe_locked(abandoned)
    assert breaker._probe_in_flight == 1
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "blocked")


def test_cancelled_probe_restarts_the_window_for_its_replacement() -> None:
    clock = ManualClock()
    breaker = _opened(clock)
    cancelled = _admit(breaker)
    clock.advance(9.9)
    with breaker._lock:
        breaker._release_probe_locked(cancelled)
    replacement = _admit(breaker)
    clock.advance(0.2)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "must wait a full window")
    with breaker._lock:
        breaker._record_call_success_locked(replacement)
    assert breaker.state() == CircuitState.CLOSED


def test_cancellations_do_not_extend_a_hung_probes_window() -> None:
    clock = ManualClock()
    breaker = _opened(clock, half_open_max_probes=2)
    _admit(breaker)  # hung, never reports
    other = _admit(breaker)
    clock.advance(9.0)
    with breaker._lock:
        breaker._release_probe_locked(other)
    _admit(breaker)
    clock.advance(1.0)
    assert breaker.call(lambda: "reclaimed") == "reclaimed"


@pytest.mark.asyncio
async def test_async_abandoned_probe_success_closes_and_window_restarts() -> None:
    clock = ManualClock()
    breaker = AsyncCircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock
    )
    await breaker.record_failure()
    clock.advance(10.0)

    release = asyncio.Event()

    async def slow() -> str:
        await release.wait()
        return "slow"

    async def fine() -> str:
        return "ok"

    async def cancelled_probe() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(breaker.call(cancelled_probe))
    await asyncio.sleep(0)
    clock.advance(9.9)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    abandoned = asyncio.create_task(breaker.call(slow))
    await asyncio.sleep(0)
    clock.advance(0.2)
    with pytest.raises(CircuitOpenError):
        await breaker.call(fine)

    clock.advance(10.0)
    current_release = asyncio.Event()

    async def current_probe() -> None:
        await current_release.wait()

    current = asyncio.create_task(breaker.call(current_probe))
    await asyncio.sleep(0)
    release.set()
    assert await abandoned == "slow"
    assert await breaker.state() == CircuitState.CLOSED
    current_release.set()
    await current


# -- Refill rounding ----------------------------------------------------------


def test_refill_accumulates_when_increment_rounds_away() -> None:
    clock = ManualClock()
    bucket = TokenBucket("b", 1e15, 1.0, clock=clock)
    assert bucket.try_acquire(1.0)
    for _ in range(40):
        clock.advance(0.05)
        assert bucket.available <= 1e15  # the read runs a refill each step
    assert bucket.available == 1e15


@pytest.mark.asyncio
async def test_async_refill_accumulates_when_increment_rounds_away() -> None:
    clock = ManualClock()
    bucket = AsyncTokenBucket("b", 1e15, 1.0, clock=clock)
    assert await bucket.try_acquire(1.0)
    for _ in range(40):
        clock.advance(0.05)
        await bucket.available()
    assert await bucket.available() == 1e15


@pytest.mark.parametrize("bucket_type", ["sync", "async"])
def test_idle_full_bucket_earns_no_credit(bucket_type: str) -> None:
    clock = ManualClock()
    if bucket_type == "sync":
        sync_bucket = TokenBucket("b", 10.0, 1.0, clock=clock)
        clock.advance(3600.0)
        assert sync_bucket.available == pytest.approx(10.0)
        assert sync_bucket.try_acquire(10.0)
        assert sync_bucket.available == pytest.approx(0.0)
        return

    async def scenario() -> None:
        async_bucket = AsyncTokenBucket("b", 10.0, 1.0, clock=clock)
        clock.advance(3600.0)
        assert await async_bucket.available() == pytest.approx(10.0)
        assert await async_bucket.try_acquire(10.0)
        assert await async_bucket.available() == pytest.approx(0.0)

    asyncio.run(scenario())


def test_probe_admitted_long_after_a_cancellation_gets_a_full_window() -> None:
    clock = ManualClock()
    breaker = _opened(clock)
    cancelled = _admit(breaker)
    with breaker._lock:
        breaker._release_probe_locked(cancelled)
    clock.advance(9.9)
    replacement = _admit(breaker)
    clock.advance(0.2)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "must wait a full window")
    with breaker._lock:
        breaker._record_call_success_locked(replacement)
    assert breaker.state() == CircuitState.CLOSED
