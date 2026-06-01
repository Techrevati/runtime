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
from typing import Any, cast

import pytest

from techrevati.runtime import (
    AgentFailureClass,
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


def test_token_bucket_normalizes_and_validates_name() -> None:
    b = TokenBucket(" bucket ", capacity=5.0, refill_per_second=1.0)
    assert b.name == "bucket"

    with pytest.raises(ValueError, match="name"):
        TokenBucket(" ", capacity=5.0, refill_per_second=1.0)
    with pytest.raises(TypeError, match="name"):
        TokenBucket(cast(Any, 123), capacity=5.0, refill_per_second=1.0)


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


@pytest.mark.parametrize("tokens", [-1.0, float("nan"), float("inf")])
def test_token_bucket_rejects_invalid_spend_amounts(tokens: float) -> None:
    b = TokenBucket("b", capacity=5.0, refill_per_second=1.0)

    with pytest.raises(ValueError):
        b.try_acquire(tokens)
    with pytest.raises(ValueError):
        b.acquire(tokens)


def test_token_bucket_rejects_bool_spend_amounts() -> None:
    b = TokenBucket("b", capacity=5.0, refill_per_second=1.0)

    with pytest.raises(TypeError, match="tokens"):
        b.try_acquire(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="tokens"):
        b.acquire(False)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [-0.1, float("nan"), float("inf")])
def test_token_bucket_rejects_invalid_timeout(timeout: float) -> None:
    b = TokenBucket("b", capacity=5.0, refill_per_second=1.0)

    with pytest.raises(ValueError):
        b.acquire(1.0, timeout=timeout)


def test_token_bucket_rejects_bool_timeout() -> None:
    b = TokenBucket("b", capacity=5.0, refill_per_second=1.0)

    with pytest.raises(TypeError, match="timeout"):
        b.acquire(1.0, timeout=True)  # type: ignore[arg-type]


def test_acquire_raises_on_timeout() -> None:
    # 1 token per second, want 10 tokens, but only 0.1s budget — must fail.
    clk = ManualClock()
    b = TokenBucket("b", capacity=10.0, refill_per_second=1.0, clock=clk)
    b.try_acquire(10.0)  # empty the bucket
    with pytest.raises(RateLimitExceededError):
        b.acquire(5.0, timeout=0.1)


def test_constructor_rejects_invalid_params() -> None:
    invalid_values = [0.0, -1.0, float("nan"), float("inf")]
    for value in invalid_values:
        with pytest.raises(ValueError, match="capacity"):
            TokenBucket("b", capacity=value, refill_per_second=1.0)
        with pytest.raises(ValueError, match="refill_per_second"):
            TokenBucket("b", capacity=1.0, refill_per_second=value)


def test_constructor_rejects_bool_params() -> None:
    with pytest.raises(TypeError, match="capacity"):
        TokenBucket("b", capacity=True, refill_per_second=1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="refill_per_second"):
        TokenBucket("b", capacity=1.0, refill_per_second=True)  # type: ignore[arg-type]


def test_constructor_rejects_invalid_clock() -> None:
    with pytest.raises(TypeError, match="clock"):
        TokenBucket("b", capacity=1.0, refill_per_second=1.0, clock=cast(Any, None))
    with pytest.raises(ValueError, match="clock"):
        TokenBucket(
            "b",
            capacity=1.0,
            refill_per_second=1.0,
            clock=lambda: float("nan"),
        )


def test_available_rejects_invalid_clock_value_after_startup() -> None:
    values = iter([0.0, float("inf")])
    b = TokenBucket(
        "b", capacity=1.0, refill_per_second=1.0, clock=lambda: next(values)
    )

    with pytest.raises(ValueError, match="clock"):
        _ = b.available


def test_rate_limit_exceeded_error_validates_payload() -> None:
    err = RateLimitExceededError(" rpm ", 1.5)
    assert err.bucket_name == "rpm"
    assert err.tokens == 1.5

    with pytest.raises(ValueError, match="bucket_name"):
        RateLimitExceededError("", 1.0)
    with pytest.raises(ValueError, match="tokens"):
        RateLimitExceededError("rpm", float("nan"))


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


def test_rate_limiter_validates_and_copies_buckets() -> None:
    rpm = TokenBucket("rpm", capacity=5.0, refill_per_second=1.0)
    buckets = {" rpm ": rpm}
    lim = RateLimiter(buckets)
    buckets.clear()

    assert lim.get("rpm") is rpm
    assert lim.get(" rpm ") is rpm
    with pytest.raises(ValueError, match="name"):
        lim.get("")

    with pytest.raises(TypeError, match="buckets"):
        RateLimiter(cast(Any, []))
    with pytest.raises(TypeError, match="TokenBucket"):
        RateLimiter(cast(Any, {"rpm": object()}))
    with pytest.raises(ValueError, match="bucket name"):
        RateLimiter(cast(Any, {" ": rpm}))


def test_rate_limiter_rejects_invalid_request_cost_even_without_bucket() -> None:
    lim = RateLimiter()

    with pytest.raises(ValueError, match="request_cost"):
        lim.acquire_pre_call(request_cost=-1.0)
    with pytest.raises(TypeError, match="request_cost"):
        lim.acquire_pre_call(request_cost=True)  # type: ignore[arg-type]


def test_rate_limiter_rejects_invalid_usage_even_without_bucket() -> None:
    lim = RateLimiter()

    with pytest.raises(ValueError, match="input_tokens"):
        lim.acquire_usage(input_tokens=-1, output_tokens=0)
    with pytest.raises(ValueError, match="output_tokens"):
        lim.acquire_usage(input_tokens=0, output_tokens=-1)


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
async def test_async_bucket_validates_name_and_clock() -> None:
    b = AsyncTokenBucket(" async ", capacity=5.0, refill_per_second=1.0)
    assert b.name == "async"

    with pytest.raises(ValueError, match="name"):
        AsyncTokenBucket(" ", capacity=5.0, refill_per_second=1.0)
    with pytest.raises(TypeError, match="clock"):
        AsyncTokenBucket(
            "async",
            capacity=5.0,
            refill_per_second=1.0,
            clock=cast(Any, None),
        )
    values = iter([0.0, float("nan")])
    broken = AsyncTokenBucket(
        "async",
        capacity=5.0,
        refill_per_second=1.0,
        clock=lambda: next(values),
    )
    with pytest.raises(ValueError, match="clock"):
        await broken.available()


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [-1.0, float("nan"), float("inf")])
async def test_async_token_bucket_rejects_invalid_spend_amounts(
    tokens: float,
) -> None:
    b = AsyncTokenBucket("ab", capacity=5.0, refill_per_second=1.0)

    with pytest.raises(ValueError):
        await b.try_acquire(tokens)
    with pytest.raises(ValueError):
        await b.acquire(tokens)


@pytest.mark.asyncio
async def test_async_token_bucket_rejects_bool_amounts() -> None:
    b = AsyncTokenBucket("ab", capacity=5.0, refill_per_second=1.0)

    with pytest.raises(TypeError, match="tokens"):
        await b.try_acquire(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="tokens"):
        await b.acquire(False)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="timeout"):
        await b.acquire(1.0, timeout=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [-0.1, float("nan"), float("inf")])
async def test_async_token_bucket_rejects_invalid_timeout(timeout: float) -> None:
    b = AsyncTokenBucket("ab", capacity=5.0, refill_per_second=1.0)

    with pytest.raises(ValueError):
        await b.acquire(1.0, timeout=timeout)


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


@pytest.mark.asyncio
async def test_async_rate_limiter_validates_and_copies_buckets() -> None:
    rpm = AsyncTokenBucket("rpm", capacity=5.0, refill_per_second=1.0)
    buckets = {" rpm ": rpm}
    lim = AsyncRateLimiter(buckets)
    buckets.clear()

    assert lim.get("rpm") is rpm
    with pytest.raises(ValueError, match="name"):
        lim.get(" ")

    with pytest.raises(TypeError, match="buckets"):
        AsyncRateLimiter(cast(Any, []))
    with pytest.raises(TypeError, match="AsyncTokenBucket"):
        AsyncRateLimiter(cast(Any, {"rpm": object()}))


@pytest.mark.asyncio
async def test_async_rate_limiter_rejects_invalid_usage_even_without_bucket() -> None:
    lim = AsyncRateLimiter()

    with pytest.raises(ValueError, match="request_cost"):
        await lim.acquire_pre_call(request_cost=float("nan"))
    with pytest.raises(TypeError, match="request_cost"):
        await lim.acquire_pre_call(request_cost=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="input_tokens"):
        await lim.acquire_usage(input_tokens=-1, output_tokens=0)
    with pytest.raises(ValueError, match="output_tokens"):
        await lim.acquire_usage(input_tokens=0, output_tokens=-1)


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


def test_orchestrator_rate_limiter_escape_marks_terminal_rate_limit() -> None:
    rpm = TokenBucket("rpm", capacity=0.5, refill_per_second=1.0)
    lim = RateLimiter({"rpm": rpm})
    orch = AgentSession(role="writer", phase="draft", rate_limiter=lim)

    with pytest.raises(RateLimitExceededError):
        with orch.session() as session:
            session.run_turn(lambda: "ok")

    failures = [
        event for event in session.events if event.event.value == "agent.failed"
    ]
    assert failures[-1].failure_class == AgentFailureClass.RATE_LIMIT
    assert failures[-1].detail == "RateLimitExceededError raised"


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


@pytest.mark.asyncio
async def test_orchestrator_async_rate_limiter_escape_marks_rate_limit() -> None:
    rpm = AsyncTokenBucket("rpm", capacity=0.5, refill_per_second=1.0)
    lim = AsyncRateLimiter({"rpm": rpm})
    orch = AgentSession(role="writer", phase="draft", async_rate_limiter=lim)

    async def call() -> str:
        return "ok"

    with pytest.raises(RateLimitExceededError):
        async with orch.asession() as session:
            await session.arun_turn(call)

    failures = [
        event for event in session.events if event.event.value == "agent.failed"
    ]
    assert failures[-1].failure_class == AgentFailureClass.RATE_LIMIT
    assert failures[-1].detail == "RateLimitExceededError raised"
