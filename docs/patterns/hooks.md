# Hooks

Hooks are the runtime's **interceptor chain**. Unlike `EventSink`
(which observes) or `Guardrail` (which blocks), a hook may **mutate**
the data flowing through a turn or tool call — redact PII before a
prompt reaches the model, log model I/O for audit, wrap a result in a
post-processing step, fail fast on a token-budget pre-flight check.

The chain runs left-to-right around every `run_turn` / `arun_turn` and
`run_tool` / `arun_tool` call. Each hook sees the post-mutation output
of the previous one.

## When to use this

- **Cross-cutting concerns** that several agents share: PII redaction,
  audit logging, request signing, cost pre-flight checks.
- **Surgical input/output rewriting** at the runtime boundary — caller
  builds the prompt; hook sanitizes it; model receives the cleaned
  version.
- **Composable safety primitives** that are too lightweight for a
  full `Guardrail` and too coupled to the value to belong in an
  `EventSink`.

## When NOT to use this

- **Heavy per-turn logic** that does substantive work the agent itself
  should orchestrate. Hooks should be fast and side-effect-clear.
- **Long-running I/O on the sync path.** Use the async hook variants
  (`abefore_model`, `aafter_tool`, …) and an `AsyncOrchestrationSession`.
- **Blocking the call entirely.** Hooks raise exceptions, which the
  caller sees as the turn's failure — but this is not the cleanest
  contract for "this content is forbidden." Use a
  [`Guardrail`](../api/guardrails.md) for that.

## Quickstart

```python
from techrevati.runtime import (
    AgentSession,
    HookContext,
    LogModelIOHook,
    RedactPIIHook,
    TokenBudgetCheckHook,
)


sess = AgentSession(
    role="writer",
    phase="draft",
    hooks=[
        RedactPIIHook(),
        LogModelIOHook(),
        TokenBudgetCheckHook(token_limit=8_000),
    ],
)

ctx = HookContext(
    role="writer",
    phase="draft",
    prompt="Email me at alice@example.com — SSN 123-45-6789.",
)

async with sess.asession() as session:
    async def call() -> str:
        # Closure reads ctx.prompt AFTER hooks have mutated it,
        # so the model sees the redacted value.
        return await acall_model(ctx.prompt)

    result, usage = await session.arun_turn(
        call, model="claude-opus-4-7", hook_ctx=ctx,
    )
```

## The five lifecycle methods

Hooks implement any subset of:

| method            | when                              | mutates                    |
|-------------------|-----------------------------------|----------------------------|
| `before_model`    | before `run_turn` / `arun_turn`   | `ctx.prompt` (in place)    |
| `after_model`     | after the model returns           | returns the new result     |
| `before_tool`     | before `run_tool` / `arun_tool`   | `ctx.args` (in place)      |
| `after_tool`      | after the tool returns            | returns the new result     |
| `before_handoff`  | before `handoff_to` / `ahandoff_to` | `ctx.extra` handoff fields |

Async sessions look up `abefore_model` / `aafter_model` first; if
absent, they fall back to the sync method and call it inline. So a
hook that implements only `before_model` works in both sync and async
sessions; a hook with only `abefore_model` works in async sessions
only (the sync path silently skips it — matching the
`AsyncGuardrail` behavior).

For handoffs, `ctx.extra` contains `target_role`, `reason`, `context`,
and read-only `project_id`. A `before_handoff` hook may raise to block
the handoff, or mutate `target_role`, `reason`, and `context` before
the target worker is registered. Use `ahandoff_to` when an async-only
`abefore_handoff` hook must run; the inherited sync `handoff_to`
method runs sync handoff hooks only.

## `HookContext`

The same mutable `HookContext` instance flows through the whole chain
for a single call. Hooks may mutate any field.

```python
@dataclass
class HookContext:
    role: str
    phase: str
    model: str = ""        # set by the session for *_model hooks
    prompt: Any = None     # caller-supplied; redacted/logged by hooks
    tool: str = ""         # set by the session for *_tool hooks
    args: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)  # free-form caller scratch
```

Pass an instance via `hook_ctx=` on `run_turn` / `arun_turn` /
`run_tool` / `arun_tool` / `arun_turn_stream` / `handoff_to` /
`ahandoff_to`. **The caller's
`coro_factory` / `fn` must close over `ctx.prompt` (or `ctx.args`) to
see the post-hook value** — the runtime does not pass the mutated
prompt into your factory, since the factory shape is unconstrained.

If `hook_ctx` is omitted, the runtime synthesizes a fresh
`HookContext` so hooks still fire — but mutations to `ctx.prompt`
have no observer.

## Built-in hooks

### `RedactPIIHook`

Best-effort PII scrubber for strings, dicts, and message dictionaries.
Defaults to SSN, email, credit card, IPv4, and long API-key
heuristics; pass `patterns=` to override.

```python
RedactPIIHook(
    patterns=[r"\b\d{3}-\d{2}-\d{4}\b"],
    replacement="[REDACTED]",
)
```

Runs in **both** `before_model` (input scrub) and `after_model`
(output scrub) so log sinks downstream cannot leak what the upstream
redactor caught.

This is a first-line defense, not a substitute for a dedicated PII
scrubber. Pattern-based redaction misses paraphrased PII and
context-aware references; for high-risk deployments, swap in or
prepend a model-based scrubber.

### `LogModelIOHook`

Logs model-call metadata via a stdlib logger. Prompt and result payloads are
off by default so production deployments do not accidentally leak model inputs
or outputs.

```python
LogModelIOHook(
    level=logging.INFO,
    include_prompt=False,
    include_result=False,
    max_chars=4_000,  # truncates oversized payloads
)
```

Set `include_prompt=True` or `include_result=True` only in a controlled debug
or audit workflow after redaction and retention policy are in place. Payloads
above `max_chars` are truncated and suffixed with `"…(truncated)"` so a runaway
100 K-token blob does not flood your log pipeline.

### `TokenBudgetCheckHook`

Pre-flight token-budget guard. Raises `HookBudgetExceededError`
**before** the model call if the estimator exceeds `token_limit`.

```python
TokenBudgetCheckHook(
    token_limit=8_000,
    estimator=tiktoken_estimator,  # default: len(str(prompt)) // 4
)
```

Composes with [`UsageLimits`](usage-tracking.md) (post-flight, per
session) and [`GovernancePlane.MaxBudgetLimit`](governance.md)
(terminal, post-flight). Use this hook to catch the obvious "I just
pasted a 50 K-token document" mistake before it costs anything.

## Writing your own hook

Hooks are plain classes. Only the `name` attribute is required;
implement only the lifecycle methods you need.

```python
class RequestSigningHook:
    name = "request_signing"

    def __init__(self, key: str) -> None:
        self._key = key

    def before_model(self, ctx: HookContext) -> None:
        ctx.extra["signature"] = hmac.new(
            self._key.encode(), str(ctx.prompt).encode(), "sha256"
        ).hexdigest()
```

`Hook` and `AsyncHook` are both `runtime_checkable` Protocols — pass
your class instance directly into `hooks=` and the dispatcher figures
out which methods are sync vs. async.

## Chain ordering

Hooks run in the order you supplied them. If hook B reads what hook A
wrote, put A first:

```python
sess = AgentSession(
    role="writer", phase="draft",
    hooks=[
        RedactPIIHook(),              # 1. redact PII from prompt
        LogModelIOHook(),             # 2. log metadata only by default
        TokenBudgetCheckHook(8_000),  # 3. budget-check the redacted prompt
    ],
)
```

The chain runs the same way for both sync and async sessions. There
is no priority system — order is the only signal.

## Exception semantics

Hooks may raise. The orchestrator does **not** catch hook exceptions
in the sync chain — they propagate through `run_turn` / `arun_turn`
exactly like any other failure (and so participate in
`classify_exception` + recovery).

In particular, `TokenBudgetCheckHook` raises
`HookBudgetExceededError`. The recovery loop sees it as a generic
exception → `UNKNOWN` failure class; if you want a different
recovery scenario (smaller context, switch provider, etc.) catch
it in the caller before re-raising into the orchestrator.

## Composing with guardrails

- A **`Guardrail`** is a yes/no check — it blocks the call when its
  outcome is `allowed=False`.
- A **`Hook`** is a transformer — it mutates inputs/outputs and can
  raise on policy violations.

Both run around tool calls; the order is **`before_tool` hooks →
pre-guardrails → tool fn → post-guardrails → `after_tool` hooks**, so
hook mutations are visible to guardrails.

## Anti-patterns

- **Hiding business logic in hooks.** Hooks are about cross-cutting
  concerns; the agent's actual reasoning belongs in the model call or
  tool implementations.
- **Mutating `ctx.role` or `ctx.phase` mid-flight.** The runtime resets
  these from the session before the chain runs; treat them as
  read-only.
- **Async I/O in a sync hook.** Use `AsyncHook` and an async session;
  blocking the event loop ruins everything streaming buys you.
- **Catching `HookBudgetExceededError` inside a hook chain.** Let it
  bubble up; the orchestrator is the only place that can decide
  whether to recover.

## See Also

- `docs/patterns/guardrails.md`
- `docs/api/hooks.md`
