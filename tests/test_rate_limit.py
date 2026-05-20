"""Tests for techrevati.runtime.rate_limit.

Sync and async variants share semantics — most tests are written against
the sync class with an injected ``ManualClock`` so they stay
deterministic; the async sibling has its own focused tests for the
async-specific bits (asyncio.sleep yielding, asyncio.Lock semantics).
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from techrevati.runtime import (
    AgentSession,
    AsyncRateLimiter,
    AsyncTokenBucket,
    RateLimiter,
    RateLimitExceededError,
    TokenBucket,
    UsageSnapshot,
)
from tests.conftest import ManualClock

# ---------------------------------------------------------------------------
# TokenBucket — sync
# ---------------------------------------------------------------------------


def test_token_bucket_starts_full() -> None:
    clk = ManualClock()
    b = TokenBucket("b", capacity=5.0, refill_per_second=1.0, clock=clk)
    assert b.available == 5.0


def test_try_acquire_spends_tokens() -> None:
    clk = ManualClock()
    b = TokenBucket("b", capacity=5.0, refill_per_second=1.0, clock=clk)
    assert b.try_acquire(3.0) is True
    assert b.available == 2.0


def test_try_acquire_fails_when_empty() -> None:
    clk = ManualClock()
    b = TokenBucket("b", capacity=2.0, refill_per_second=1.0, clock=clk)
    b.try_acquire(2.0)
    assert b.try_acquire(1.0) is False


def test_refill_caps_at_capacity() -> None:
    clk = ManualClock()
    b = TokenBucket("b", capacity=5.0, refill_per_second=10.0, clock=clk)
    b.try_acquire(5.0)
    clk.advance(1000.0)
    assert b.available == 5.0


def test_acquire_raises_when_request_exceeds_capacity() -> None:
    b = TokenBucket("b", capacity=5.0, refill_per_second=1.0)
    with pytest.raises(RateLimitExceededError):
        b.acquire(10.0)


def test_acquire_raises_on_timeout() -> None:
    # 1 token per second, want 10 tokens, but only 0.1s budget — must fail.
    clk = ManualClock()
    b = TokenBucket("b", capacity=10.0, refill_per_second=1.0, clock=clk)
    b.try_acquire(10.0)  # empty the bucket
    with pytest.raises(RateLimitExceededError):
        b.acquire(5.0, timeout=0.1)


def test_constructor_rejects_invalid_params() -> None:
    with pytest.raises(ValueError):
        TokenBucket("b", capacity=0.0, refill_per_second=1.0)
    with pytest.raises(ValueError):
        TokenBucket("b", capacity=1.0, refill_per_second=-1.0)


def test_zero_tokens_is_a_no_op() -> None:
    b = TokenBucket("b", capacity=1.0, refill_per_second=1.0)
    assert b.try_acquire(0) is True
    b.acquire(0)  # no exception, no sleep


def test_token_bucket_thread_safety() -> None:
    """Concurrent try_acquire calls must never let total spending exceed cap."""
    b = TokenBucket("b", capacity=100.0, refill_per_second=0.001)
    successes = 0
    lock = threading.Lock()

    def worker() -> None:
        nonlocal successes
        if b.try_acquire(1.0):
            with lock:
                successes += 1

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert successes == 100  # exactly the capacity, no overshoot


# ---------------------------------------------------------------------------
# RateLimiter composite
# ---------------------------------------------------------------------------


def test_rate_limiter_acquire_pre_call_consumes_rpm_only() -> None:
    clk = ManualClock()
    rpm = TokenBucket("rpm", capacity=5.0, refill_per_second=1.0, clock=clk)
    tpm = TokenBucket("input_tpm", capacity=100.0, refill_per_second=1.0, clock=clk)
    lim = RateLimiter({"rpm": rpm, "input_tpm": tpm})
    lim.acquire_pre_call()
    assert rpm.available == pytest.approx(4.0)
    assert tpm.available == pytest.approx(100.0)


def test_rate_limiter_acquire_usage_consumes_input_and_output_tpm() -> None:
    clk = ManualClock()
    in_b = TokenBucket("input_tpm", capacity=1000.0, refill_per_second=1.0, clock=clk)
    out_b = TokenBucket("output_tpm", capacity=1000.0, refill_per_second=1.0, clock=clk)
    lim = RateLimiter({"input_tpm": in_b, "output_tpm": out_b})
    lim.acquire_usage(input_tokens=300, output_tokens=100)
    assert in_b.available == pytest.approx(700.0)
    assert out_b.available == pytest.approx(900.0)


def test_rate_limiter_empty_buckets_is_a_noop() -> None:
    lim = RateLimiter()
    lim.acquire_pre_call()
    lim.acquire_usage(input_tokens=1_000_000, output_tokens=1_000_000)


# ---------------------------------------------------------------------------
# AsyncTokenBucket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_bucket_basic_spend_and_refill() -> None:
    clk = ManualClock()
    b = AsyncTokenBucket("ab", capacity=5.0, refill_per_second=10.0, clock=clk)
    assert await b.try_acquire(3.0) is True
    assert await b.available() == 2.0
    clk.advance(1.0)
    assert await b.available() == 5.0  # clamped at capacity


@pytest.mark.asyncio
async def test_async_acquire_yields_event_loop() -> None:
    """``acquire`` must use ``asyncio.sleep`` so a sibling task can run."""
    b = AsyncTokenBucket("ab", capacity=1.0, refill_per_second=20.0)
    await b.try_acquire(1.0)  # empty

    other_ran = False

    async def other() -> None:
        nonlocal other_ran
        await asyncio.sleep(0)
        other_ran = True

    started = time.monotonic()
    await asyncio.gather(b.acquire(1.0), other())
    elapsed = time.monotonic() - started
    assert other_ran is True
    # The bucket needed ~50ms to refill (1 / 20 per second). We're not
    # being strict about the upper bound — just that the sibling got to
    # run while we waited, which is the whole point of the async impl.
    assert elapsed < 0.5, f"acquire blocked too long: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_async_rate_limiter_pre_and_usage() -> None:
    clk = ManualClock()
    rpm = AsyncTokenBucket("rpm", capacity=10.0, refill_per_second=1.0, clock=clk)
    in_b = AsyncTokenBucket(
        "input_tpm", capacity=1000.0, refill_per_second=1.0, clock=clk
    )
    lim = AsyncRateLimiter({"rpm": rpm, "input_tpm": in_b})
    await lim.acquire_pre_call()
    await lim.acquire_usage(input_tokens=300, output_tokens=0)
    assert await rpm.available() == pytest.approx(9.0)
    assert await in_b.available() == pytest.approx(700.0)


# ---------------------------------------------------------------------------
# AgentSession integration
# ---------------------------------------------------------------------------


def test_orchestrator_consumes_rpm_pre_call_and_tpm_post_call() -> None:
    clk = ManualClock()
    rpm = TokenBucket("rpm", capacity=10.0, refill_per_second=0.001, clock=clk)
    in_b = TokenBucket(
        "input_tpm", capacity=10_000.0, refill_per_second=0.001, clock=clk
    )
    out_b = TokenBucket(
        "output_tpm", capacity=10_000.0, refill_per_second=0.001, clock=clk
    )
    lim = RateLimiter({"rpm": rpm, "input_tpm": in_b, "output_tpm": out_b})
    orch = AgentSession(role="writer", phase="draft", rate_limiter=lim)
    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            usage=UsageSnapshot(input_tokens=400, output_tokens=200),
        )
    assert rpm.available == pytest.approx(9.0)
    assert in_b.available == pytest.approx(9_600.0)
    assert out_b.available == pytest.approx(9_800.0)


@pytest.mark.asyncio
async def test_orchestrator_async_session_consumes_async_limiter() -> None:
    clk = ManualClock()
    rpm = AsyncTokenBucket("rpm", capacity=10.0, refill_per_second=0.001, clock=clk)
    in_b = AsyncTokenBucket(
        "input_tpm", capacity=10_000.0, refill_per_second=0.001, clock=clk
    )
    lim = AsyncRateLimiter({"rpm": rpm, "input_tpm": in_b})
    orch = AgentSession(role="writer", phase="draft", async_rate_limiter=lim)

    async def call() -> str:
        return "ok"

    async with orch.asession() as session:
        await session.arun_turn(
            call,
            usage=UsageSnapshot(input_tokens=250, output_tokens=0),
        )
    assert await rpm.available() == pytest.approx(9.0)
    assert await in_b.available() == pytest.approx(9_750.0)
