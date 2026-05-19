# Circuit Breaker

Fault-tolerant execution wrapper. Three states: `CLOSED` (normal), `OPEN` (failing, blocks new requests), `HALF_OPEN` (testing, allows one probe). Transitions are thread-safe.

## Usage

```python
from techrevati.runtime import CircuitBreaker, CircuitOpenError

cb = CircuitBreaker(
    name="downstream",
    failure_threshold=5,
    recovery_timeout_seconds=60.0,
)

try:
    result = cb.call(fetch, url, timeout=10)
except CircuitOpenError:
    result = fallback()
```

Each consecutive failure increments an internal counter. At `failure_threshold` failures, the breaker opens and immediately raises `CircuitOpenError` without calling `fn`. After `recovery_timeout_seconds`, the next call moves the breaker to `HALF_OPEN`; a successful call closes it again, a failure re-opens it.

## API

```python
CircuitBreaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout_seconds: float = 60.0,
)
```

| Method | Behavior |
|---|---|
| `call(fn, *args, **kwargs)` | Execute `fn` if the breaker permits, otherwise raise `CircuitOpenError` |
| `record_success()` | Manual: reset failure count; close if in HALF_OPEN |
| `record_failure()` | Manual: bump failure count; open at threshold |
| `state()` | Current state (`CLOSED` / `OPEN` / `HALF_OPEN`) |
| `is_open()` | Convenience boolean |
| `reset()` | Force CLOSED with counters cleared |

## When to use it

- One breaker per external dependency (a host, an endpoint, a database).
- Don't share a breaker across unrelated calls; you'll over-trip it.
- Pair with a retry recipe (`retry_policy`) — the breaker handles "stop hitting a dead service"; the recipe handles "what to do instead".

## Notes

- `failure_threshold` is consecutive failures. A single success resets the counter.
- The HALF_OPEN probe is *one* request. If you need N probes, wrap in your own counter.
