"""
Rate limiting — Token-bucket primitives for sync and async call paths.

Token-aware throttling is the modern shape for LLM-provider rate limits
because providers themselves are token-based (TPM, RPM, daily caps). A
single ``TokenBucket`` admits or delays one resource (e.g. input
tokens-per-minute); a ``RateLimiter`` composes three named buckets so
typical provider limits (input TPM, output TPM, request RPM) can be
expressed as one object.

Both ``TokenBucket`` (sync, ``threading.Lock``) and ``AsyncTokenBucket``
(``asyncio.Lock`` + ``asyncio.sleep``) implement the same conceptual
algorithm; async wins by yielding the event loop while waiting for
refill instead of blocking it.

Clock is injectable on both variants (``Callable[[], float]`` returning
monotonic seconds). Tests pass a ``ManualClock`` to make timing-dependent
behavior deterministic; production code uses ``time.monotonic`` by
default.

Zero new runtime dependencies — stdlib only.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AsyncTokenBucket",
    "RateLimitExceededError",
    "RateLimiter",
    "TokenBucket",
]


# A single debit above this is a bug in the caller, not a real turn.
_MAX_DEBIT_TOKENS = 1e15
# Debt is capped at this much refill. Past some size a float balance stops
# changing when refill is added, so an uncapped debt could never be repaid and
# every later acquire would block for good.
_MAX_DEBT_SECONDS = 86_400.0


class RateLimitExceededError(Exception):
    """Raised when an ``acquire`` call exceeds the bucket's wait budget.

    Carries the bucket name and the cost the caller tried to spend so
    the error message tells the caller which dimension blocked (input
    TPM vs RPM) and how big the request was. When the error escapes an
    ``AgentSession``, the terminal event uses the public ``rate_limit``
    failure class.
    """

    def __init__(self, bucket_name: str, tokens: float) -> None:
        bucket_name = _validate_bucket_name("bucket_name", bucket_name)
        tokens = _validate_amount("tokens", tokens, allow_zero=True)
        self.bucket_name = bucket_name
        self.tokens = tokens
        super().__init__(
            f"rate limit exceeded on '{bucket_name}': requested {tokens:g} tokens"
        )

    def __reduce__(self) -> tuple[type[RateLimitExceededError], tuple[str, float]]:
        return (type(self), (self.bucket_name, self.tokens))


def _validate_bucket_name(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value.strip()


def _validate_amount(name: str, value: float, *, allow_zero: bool) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a finite number")
    try:
        amount = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    if allow_zero:
        if amount < 0:
            raise ValueError(f"{name} must be >= 0")
    elif amount <= 0:
        raise ValueError(f"{name} must be > 0")
    return amount


def _validate_debit(name: str, value: float) -> float:
    amount = _validate_amount(name, value, allow_zero=True)
    if amount > _MAX_DEBIT_TOKENS:
        raise ValueError(f"{name} must be <= {_MAX_DEBIT_TOKENS:g}")
    return amount


def _debited_balance(balance: float, tokens: float, refill_per_second: float) -> float:
    """Subtract ``tokens``, keeping the debt repayable (see ``_MAX_DEBT_SECONDS``)."""
    return max(balance - tokens, -refill_per_second * _MAX_DEBT_SECONDS)


def _validate_timeout(timeout: float | None) -> float | None:
    if timeout is None:
        return None
    return _validate_amount("timeout", timeout, allow_zero=True)


def _validate_clock(clock: Callable[[], float]) -> Callable[[], float]:
    if not callable(clock):
        raise TypeError("clock must be callable")
    return clock


def _clock_now(clock: Callable[[], float]) -> float:
    """Read the clock. Any finite value is valid: the epoch is arbitrary."""
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("clock must be a finite number")
    now = float(value)
    if not math.isfinite(now):
        raise ValueError("clock must be finite")
    return now


@dataclass
class TokenBucket:
    """Classic token-bucket limiter — sync variant.

    ``try_acquire`` is non-blocking and returns ``True`` only when the
    bucket has enough tokens. ``acquire`` sleeps until the bucket
    refills, capped by an optional wait timeout; on timeout it raises
    ``RateLimitExceededError`` rather than silently exceeding the
    bound.

    Parameters
    ----------
    name:
        Human-readable identifier used in error messages.
    capacity:
        Maximum tokens the bucket holds. Bursts up to this many
        requests can pass immediately.
    refill_per_second:
        Steady-state admission rate.
    clock:
        Monotonic time source. Defaults to ``time.monotonic``.
    """

    name: str
    capacity: float
    refill_per_second: float
    clock: Callable[[], float] = field(default=time.monotonic)

    _tokens: float = field(init=False, repr=False)
    _last_refill: float = field(init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.name = _validate_bucket_name("name", self.name)
        self.capacity = _validate_amount("capacity", self.capacity, allow_zero=False)
        self.refill_per_second = _validate_amount(
            "refill_per_second", self.refill_per_second, allow_zero=False
        )
        self.clock = _validate_clock(self.clock)
        self._tokens = self.capacity
        self._last_refill = _clock_now(self.clock)

    def _refill(self) -> None:
        now = _clock_now(self.clock)
        elapsed = max(0.0, now - self._last_refill)
        if elapsed > 0:
            refilled = min(
                self.capacity, self._tokens + elapsed * self.refill_per_second
            )
            # Keep the elapsed time if the increment rounded away on a large
            # balance: it must accumulate, or a small deficit never refills. A
            # full bucket is the exception, since idle time there earns nothing.
            if refilled != self._tokens or refilled >= self.capacity:
                self._tokens = refilled
                self._last_refill = now

    @property
    def available(self) -> float:
        """Current token balance (mostly useful for diagnostics + tests)."""
        with self._lock:
            self._refill()
            return self._tokens

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Spend ``tokens`` if available; return whether the spend succeeded."""
        tokens = _validate_amount("tokens", tokens, allow_zero=True)
        if tokens == 0:
            return True
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0, *, timeout: float | None = None) -> None:
        """Block until ``tokens`` are available, or raise on timeout.

        ``timeout`` is the maximum wall-clock time we will sleep
        waiting for refill; ``None`` waits indefinitely.
        """
        tokens = _validate_amount("tokens", tokens, allow_zero=True)
        timeout = _validate_timeout(timeout)
        if tokens == 0:
            return
        if tokens > self.capacity:
            # Caller is asking for more than the bucket can ever hold.
            raise RateLimitExceededError(self.name, tokens)
        deadline = self.clock() + timeout if timeout is not None else None
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self.refill_per_second
            if deadline is not None:
                remaining = deadline - self.clock()
                if remaining <= 0 or wait > remaining:
                    raise RateLimitExceededError(self.name, tokens)
            # Use the same clock-driven sleep as ``time.sleep`` — tests
            # that inject a manual clock should drive refill via
            # clock.advance + a fake sleeper to keep the suite fast.
            time.sleep(min(wait, 0.05))

    def debit(self, tokens: float) -> None:
        """Record ``tokens`` that were already spent, without waiting.

        Unlike ``acquire``, this cannot refuse: the spend already happened, so
        the balance may go negative (even past ``-capacity``). Later acquires
        wait until refill has paid the debt. The debt is capped at 24 hours of
        refill so it always stays repayable; anything beyond that is forgotten.
        A single debit above ``1e15`` tokens raises ``ValueError``.
        """
        tokens = _validate_debit("tokens", tokens)
        with self._lock:
            self._refill()
            self._tokens = _debited_balance(
                self._tokens, tokens, self.refill_per_second
            )

    def _settle(self, amount: float, *, timeout: float | None) -> None:
        """Wait until any debt is repaid; ``amount`` only labels the error."""
        deadline = self.clock() + timeout if timeout is not None else None
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 0:
                    return
                wait = -self._tokens / self.refill_per_second
            if deadline is not None:
                remaining = deadline - self.clock()
                if remaining <= 0 or wait > remaining:
                    raise RateLimitExceededError(self.name, amount)
            time.sleep(min(wait, 0.05))


@dataclass
class AsyncTokenBucket:
    """Async sibling of ``TokenBucket``.

    Uses ``asyncio.Lock`` so refill bookkeeping is coroutine-safe, and
    ``asyncio.sleep`` so waiting yields control to the event loop
    instead of pinning the thread. State is independent from the sync
    variant — choose one per downstream.
    """

    name: str
    capacity: float
    refill_per_second: float
    clock: Callable[[], float] = field(default=time.monotonic)

    _tokens: float = field(init=False, repr=False)
    _last_refill: float = field(init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.name = _validate_bucket_name("name", self.name)
        self.capacity = _validate_amount("capacity", self.capacity, allow_zero=False)
        self.refill_per_second = _validate_amount(
            "refill_per_second", self.refill_per_second, allow_zero=False
        )
        self.clock = _validate_clock(self.clock)
        self._tokens = self.capacity
        self._last_refill = _clock_now(self.clock)

    def _refill(self) -> None:
        now = _clock_now(self.clock)
        elapsed = max(0.0, now - self._last_refill)
        if elapsed > 0:
            refilled = min(
                self.capacity, self._tokens + elapsed * self.refill_per_second
            )
            # Keep the elapsed time if the increment rounded away on a large
            # balance: it must accumulate, or a small deficit never refills. A
            # full bucket is the exception, since idle time there earns nothing.
            if refilled != self._tokens or refilled >= self.capacity:
                self._tokens = refilled
                self._last_refill = now

    async def available(self) -> float:
        async with self._lock:
            self._refill()
            return self._tokens

    async def try_acquire(self, tokens: float = 1.0) -> bool:
        tokens = _validate_amount("tokens", tokens, allow_zero=True)
        if tokens == 0:
            return True
        async with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    async def acquire(
        self, tokens: float = 1.0, *, timeout: float | None = None
    ) -> None:
        tokens = _validate_amount("tokens", tokens, allow_zero=True)
        timeout = _validate_timeout(timeout)
        if tokens == 0:
            return
        if tokens > self.capacity:
            raise RateLimitExceededError(self.name, tokens)
        deadline = self.clock() + timeout if timeout is not None else None
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self.refill_per_second
            if deadline is not None:
                remaining = deadline - self.clock()
                if remaining <= 0 or wait > remaining:
                    raise RateLimitExceededError(self.name, tokens)
            await asyncio.sleep(min(wait, 0.05))

    async def debit(self, tokens: float) -> None:
        """Async sibling of ``TokenBucket.debit``, with the same debt cap."""
        tokens = _validate_debit("tokens", tokens)
        async with self._lock:
            self._refill()
            self._tokens = _debited_balance(
                self._tokens, tokens, self.refill_per_second
            )

    async def _settle(self, amount: float, *, timeout: float | None) -> None:
        """Wait until any debt is repaid; ``amount`` only labels the error."""
        deadline = self.clock() + timeout if timeout is not None else None
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 0:
                    return
                wait = -self._tokens / self.refill_per_second
            if deadline is not None:
                remaining = deadline - self.clock()
                if remaining <= 0 or wait > remaining:
                    raise RateLimitExceededError(self.name, amount)
            await asyncio.sleep(min(wait, 0.05))


@dataclass
class RateLimiter:
    """Composite of named token buckets, one per dimension.

    Typical LLM-provider shape: ``rpm`` for requests-per-minute,
    ``input_tpm`` for input tokens-per-minute, ``output_tpm`` for
    output tokens-per-minute. Each bucket is independent; an empty
    ``buckets`` mapping is a valid no-op limiter.

    ``acquire_pre_call`` spends RPM up front. After the call returns
    and ``UsageSnapshot`` is known, ``acquire_usage`` spends the input
    + output token budgets. This split mirrors how providers actually
    enforce limits.
    """

    buckets: dict[str, TokenBucket] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.buckets, dict):
            raise TypeError("buckets must be a dictionary")
        copied: dict[str, TokenBucket] = {}
        for name, bucket in self.buckets.items():
            bucket_name = _validate_bucket_name("bucket name", name)
            if not isinstance(bucket, TokenBucket):
                raise TypeError("buckets must contain TokenBucket instances")
            copied[bucket_name] = bucket
        self.buckets = copied

    def get(self, name: str) -> TokenBucket | None:
        return self.buckets.get(_validate_bucket_name("name", name))

    def acquire_pre_call(
        self, *, request_cost: float = 1.0, timeout: float | None = None
    ) -> None:
        """Block on RPM bucket (``buckets["rpm"]``) if configured."""
        request_cost = _validate_amount("request_cost", request_cost, allow_zero=True)
        timeout = _validate_timeout(timeout)
        rpm = self.buckets.get("rpm")
        if rpm is not None:
            rpm.acquire(request_cost, timeout=timeout)

    def acquire_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        timeout: float | None = None,
    ) -> None:
        """Charge a completed turn's tokens, then wait out any resulting debt.

        The tokens are already spent, so every configured bucket is debited
        first and the amount may exceed ``capacity`` (the balance goes
        negative). ``timeout`` is one budget shared across the buckets. On
        timeout ``RateLimitExceededError`` is raised and the debits stand,
        because the provider has counted those tokens either way.
        """
        input_amount = _validate_debit("input_tokens", input_tokens)
        output_amount = _validate_debit("output_tokens", output_tokens)
        timeout = _validate_timeout(timeout)
        charges = [
            (bucket, amount)
            for bucket, amount in (
                (self.buckets.get("input_tpm"), input_amount),
                (self.buckets.get("output_tpm"), output_amount),
            )
            if bucket is not None and amount > 0
        ]
        for bucket, amount in charges:
            bucket.debit(amount)
        remaining = timeout
        for bucket, amount in charges:
            started = bucket.clock()
            bucket._settle(amount, timeout=remaining)
            if remaining is not None:
                remaining = max(0.0, remaining - (bucket.clock() - started))


@dataclass
class AsyncRateLimiter:
    """Async sibling of ``RateLimiter`` — same semantics, async buckets."""

    buckets: dict[str, AsyncTokenBucket] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.buckets, dict):
            raise TypeError("buckets must be a dictionary")
        copied: dict[str, AsyncTokenBucket] = {}
        for name, bucket in self.buckets.items():
            bucket_name = _validate_bucket_name("bucket name", name)
            if not isinstance(bucket, AsyncTokenBucket):
                raise TypeError("buckets must contain AsyncTokenBucket instances")
            copied[bucket_name] = bucket
        self.buckets = copied

    def get(self, name: str) -> AsyncTokenBucket | None:
        return self.buckets.get(_validate_bucket_name("name", name))

    async def acquire_pre_call(
        self, *, request_cost: float = 1.0, timeout: float | None = None
    ) -> None:
        request_cost = _validate_amount("request_cost", request_cost, allow_zero=True)
        timeout = _validate_timeout(timeout)
        rpm = self.buckets.get("rpm")
        if rpm is not None:
            await rpm.acquire(request_cost, timeout=timeout)

    async def acquire_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        timeout: float | None = None,
    ) -> None:
        input_amount = _validate_debit("input_tokens", input_tokens)
        output_amount = _validate_debit("output_tokens", output_tokens)
        timeout = _validate_timeout(timeout)
        charges = [
            (bucket, amount)
            for bucket, amount in (
                (self.buckets.get("input_tpm"), input_amount),
                (self.buckets.get("output_tpm"), output_amount),
            )
            if bucket is not None and amount > 0
        ]
        for bucket, amount in charges:
            await bucket.debit(amount)
        remaining = timeout
        for bucket, amount in charges:
            started = bucket.clock()
            await bucket._settle(amount, timeout=remaining)
            if remaining is not None:
                remaining = max(0.0, remaining - (bucket.clock() - started))


# Keep AsyncRateLimiter discoverable through the package without
# exporting two near-identical class names at the top level. Callers
# that need it can import from this module directly.
_ = Any  # placate import-flatness check; kept for future signature extensions
