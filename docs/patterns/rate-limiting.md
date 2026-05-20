# Rate limiting

`TokenBucket` and its async sibling `AsyncTokenBucket` are classic
token-bucket limiters wired to be reusable across the runtime —
sessions consume them per-turn so RPM, input-TPM and output-TPM caps
match the way LLM providers actually enforce limits.

## Quick example

```python
from techrevati.runtime import (
    Orchestrator, RateLimiter, TokenBucket, UsageSnapshot,
)

limiter = RateLimiter({
    "rpm":        TokenBucket("rpm",        capacity=60,     refill_per_second=1.0),
    "input_tpm":  TokenBucket("input_tpm",  capacity=200_000, refill_per_second=3_333.0),
    "output_tpm": TokenBucket("output_tpm", capacity=60_000,  refill_per_second=1_000.0),
})

orch = Orchestrator(role="writer", phase="draft", rate_limiter=limiter)

with orch.session() as session:
    text, usage = session.run_turn(
        lambda: call_model(prompt),
        usage=UsageSnapshot(input_tokens=4_000, output_tokens=900),
    )
```

The session spends 1 token from `"rpm"` before calling the model and
4 000 / 900 from `"input_tpm"` / `"output_tpm"` after the snapshot is
known. Empty buckets block until refill (or raise
`RateLimitExceededError` when an explicit `timeout` is set).

## When to use

- Provider quotas — most paid LLM endpoints publish RPM + TPM caps,
  and 2026 providers have moved to token accounting first.
- Self-throttling to stay below per-tenant fairness limits before the
  provider returns 429.
- Smoothing bursty agent loops so a single user can't starve other
  tenants.

## When NOT to use

- One-off calls — `time.sleep` between requests is simpler and cheaper
  than carrying a bucket around.
- Distributed rate limits — these buckets are per-process. Multiple
  workers need a shared store (Redis, DBMS), wrapped in your own
  `TokenBucket`-shaped adapter; the protocol is intentionally small.
- Hard guarantees against malicious clients — buckets bound your own
  spend, not theirs.

## Async vs sync

Choose one per code path. `AsyncTokenBucket` uses `asyncio.Lock` +
`asyncio.sleep`, so waiting yields the event loop instead of pinning
it; `TokenBucket` uses `threading.Lock` + `time.sleep`. State is
independent (no shared counters), so a sync and an async bucket
pointing at the same provider must be kept in sync by you — or just
use one shape.

## Tuning

| Knob | Default | When to touch |
|---|---|---|
| `capacity` | required | Max burst you want to admit instantly. Set near the provider's 1-minute cap. |
| `refill_per_second` | required | Steady-state admission rate; divide the provider's per-minute cap by 60. |
| `acquire(..., timeout=...)` | `None` (wait forever) | Set when you'd rather fail fast than queue indefinitely. |

## Anti-patterns

- **Reusing the same `TokenBucket` instance across both sync and async
  sessions.** The two locks are different types; collisions are silent.
  Construct one bucket per code path.
- **Setting `refill_per_second` higher than `capacity`.** The bucket
  refills instantly and the limit has no effect.
- **Sleeping in a custom callback to "smooth" between turns.** The
  bucket already does this; the extra sleep stacks on top.

## See also

- [Routing](../patterns/routing.md) — failover provider selection.
- [Retry policy](../patterns/retry.md) — `RateLimitExceededError`
  maps to `FailureScenario.LLM_ERROR`.
