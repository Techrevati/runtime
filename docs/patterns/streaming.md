# Streaming

`AsyncOrchestrationSession.arun_turn_stream` reemits caller-produced
text chunks as a structured `StreamEvent` sequence. Consumers iterate
with `async for` and get one terminal `final` event after the upstream
generator exhausts, carrying the resolved usage snapshot.

This is the runtime's only streaming surface in 0.3.0. Sync sessions
do **not** have a streaming variant — sync streams require thread
coordination that conflicts with the rest of the synchronous code path
and is rarely what callers actually want.

## When to use this

- You have a provider client that exposes a token-by-token stream and want to
  surface deltas to a UI before the full response lands.
- You want to keep hook + governance + usage accounting wiring even
  when the model call is streaming. `arun_turn_stream` ticks the
  iteration cap, governance pre-turn, and rate-limiter pre-call hooks
  identically to `arun_turn`.

## When NOT to use this

- For non-streaming model calls. `arun_turn` is simpler and gives you
  the same hook / governance / cancellation guarantees with one round
  trip instead of an async generator.
- For per-chunk hook execution. Hooks fire **once before** and **once
  after** the stream — never per chunk. Per-chunk hooks would invert
  the cost model that makes streaming worthwhile.
- When the caller does not need partial output. If you discard text
  deltas, the only thing the stream is buying you is allocator churn.

## Quickstart

```python
from collections.abc import AsyncIterator

from techrevati.runtime import AgentSession, StreamEvent, UsageSnapshot


async def chunks() -> AsyncIterator[str]:
    # Wraps your provider stream — yield text deltas as they arrive.
    async for delta in client.messages.stream(prompt):
        yield delta.text


session_factory = AgentSession(role="writer", phase="draft")

async with session_factory.asession() as session:
    async for event in session.arun_turn_stream(
        chunks,
        model="your-model",
        usage=UsageSnapshot(input_tokens=1200, output_tokens=350),
    ):
        if event.type == "text_delta":
            print(event.payload["delta"], end="", flush=True)
        elif event.type == "final":
            print(f"\n[{event.payload['status']}]")
```

## Event types

`StreamEvent.type` is one of:

| type          | payload keys                                              | when |
|---------------|-----------------------------------------------------------|------|
| `text_delta`  | `delta` (str)                                             | every chunk reemitted from upstream |
| `tool_call`   | `tool` (str), `args` (dict)                               | reserved; not auto-emitted in 0.3.0 |
| `tool_result` | `tool` (str), `result` (Any)                              | reserved; not auto-emitted in 0.3.0 |
| `handoff`     | `target_role` (str), `reason` (str)                       | reserved; not auto-emitted in 0.3.0 |
| `final`       | `status` (`completed`/`cancelled`/`failed`); optional `usage`, `detail` | always last event on success or failed paths |
| `error`       | `error_type` (str), `message` (str)                       | upstream raised mid-stream |

Use the classmethod constructors (`StreamEvent.text`, `StreamEvent.final`,
…) to build events in custom plumbing — they enforce the payload shape.

## Cancellation

If the consumer breaks out of the `async for` loop, **wrap the
generator with `contextlib.aclosing`** so cleanup runs deterministically:

```python
from contextlib import aclosing

async with aclosing(
    session.arun_turn_stream(chunks, model="your-model")
) as stream:
    async for event in stream:
        if should_stop(event):
            break

assert session._last_stream_cancelled is True
```

Without `aclosing` (or an explicit `await stream.aclose()`), Python
leaves the generator dangling until garbage collection — the upstream
provider connection might not close on time and the
`_last_stream_cancelled` flag may not flip in your test or audit hook.
This is a Python-level idiom, not a runtime quirk, but the runtime
relies on it for clean shutdown.

On the cancelled path the generator does **not** yield a
`final("cancelled")` event because the consumer is no longer listening.
Check `session._last_stream_cancelled` instead.

## Errors and timeouts

If the upstream generator raises, the stream yields:

1. `StreamEvent.error(error_type=..., message=...)`
2. `StreamEvent.final(status="failed", detail=...)`
3. then re-raises so the caller's `async for` propagates the exception.

The stream payload uses a sanitized diagnostic such as
`RuntimeError raised` for `message` and `detail`; the original exception
is still re-raised to the caller, but its raw text is not copied into
stream events.

A `timeout=` keyword wraps the entire stream in `asyncio.timeout`.
Timeout fires the same error+final sequence and raises
`TurnTimeoutError` once the consumer pulls past the final event.

## Hooks with streaming

`before_model` runs **once before** the stream starts; `after_model`
runs **once after** upstream exhausts, with the joined text as its
`result`. Hooks may mutate the joined text — the mutated value is what
the `final` event's usage snapshot is computed against.

```python
class TrimWhitespaceHook:
    name = "trim"

    def before_model(self, ctx):
        # Caller closes over ctx.prompt; runtime does not pass it
        # through `chunks()` automatically.
        pass

    def after_model(self, ctx, result):
        return result.strip()
```

Per-chunk transforms (token filtering, live redaction, etc.) belong
**inside** your `chunks` async generator — wrap the upstream there.
The runtime stays out of the per-chunk hot path on purpose.

## Composing with usage tracking and governance

`arun_turn_stream` participates fully in the session's bookkeeping:

- The iteration counter ticks once before the stream starts (same as
  `arun_turn`), so a streaming turn counts toward `max_iterations`.
- `GovernancePlane.record_turn_start()` fires before; `record_success`
  or `record_failure` fires after. Cost is delta'd against
  `tracker.total_cost()` post-stream.
- `UsageLimits` and `UsageSink` are evaluated after the joined text is
  computed (you control the snapshot via the `usage=` or
  `estimate_usage=` kwargs).
- `AsyncRateLimiter.acquire_pre_call()` and `acquire_usage()` run at
  the matching points — exactly once each, not per chunk.

## Anti-patterns

- **Yielding `StreamEvent` from your `chunks` generator.** Yield raw
  text. The runtime wraps each chunk into `StreamEvent.text(...)` for
  you; if you yield events directly they will be coerced to strings or
  produce confused `text_delta` payloads.
- **Forgetting `aclosing`.** Without it, cancellation is non-deterministic.
- **Using a sync `for` loop in `chunks`.** `chunks` must be an `async
  def` generator. A sync iterator over a network stream blocks the event
  loop and defeats the purpose of streaming.
- **Mixing streaming and non-streaming turns in the same session
  without thinking about ordering.** Both touch the same iteration
  counter, governance plane, and usage tracker.

## See Also

- `docs/api/streaming.md`
- `docs/patterns/hooks.md`
