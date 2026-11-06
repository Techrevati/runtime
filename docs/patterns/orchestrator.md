# Orchestrator

Author: Techrevati doo

`AgentSession` and `OrchestrationSession` (sync) /
`AsyncOrchestrationSession` (async) wire the other primitives into a
single execution loop. Use them when you want lifecycle tracking,
retry classification, circuit-breaker protection, permission gating,
guardrails, max-iterations cap, telemetry sinks, and cost accounting in
one place.

> **Naming note.** `AgentSession` is the canonical session factory. The
> deprecated `Orchestrator` compatibility alias was removed in 0.4.0 — see the
> [0.3.x migration guide](../migrating-from-0.3.x.md). Use `session.handoff_to(...)`
> or async `session.ahandoff_to(...)` to connect multiple sessions.

## Quick example (sync)

```python
from techrevati.runtime import (
    AgentSession, UsageSnapshot, CircuitBreaker,
    register_pricing, ModelPricing,
)

register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))

agent = AgentSession(
    role="writer",
    phase="draft",
    project_id=42,
    circuit_breaker=CircuitBreaker("model-api", failure_threshold=5),
    budget_usd=10.0,
    enforce_budget=True,
    max_iterations=25,
)

with agent.session() as session:
    result, usage = session.run_turn(
        lambda: call_model(prompt),
        model="model-a",
        usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
        timeout=30.0,
    )

print(session.summary())
```

Entering the session transitions the worker through
`INITIALIZING → RUNNING`. On clean exit, to `COMPLETED`. On exception,
to `FAILED` with the error classified into an `AgentFailureClass`.
On `asyncio.CancelledError`, to `CANCELLED` with
`failure_class="cancelled"` on the terminal `agent.failed` audit event.
The event stream emits `agent.started` when the session opens and
automatically attaches `project_id` to events when configured.
`pause_for_input(...)` emits `agent.blocked` while waiting and
`agent.ready` after input arrives, without copying the prompt or
response into event data.
Budget, usage-limit, and runtime rate-limiter overruns are classified as
`failure_class="rate_limit"` on terminal session failure. Budget and
usage-limit overruns also emit an immediate rate-limit event with limit
metadata before the terminal session failure when the exception escapes the
context.
Uncaught policy and safety stops use specific terminal classifications:
`permission_denied`, `guardrail_violation`, and `governance_breach`.
If `AgentSession.max_iterations` escapes the session context, its terminal
`agent.failed` event is also classified as `governance_breach` so runaway-loop
stops do not look like model or provider failures.
Fallback `ValueError` and `TypeError` terminal failures are classified as
`validation_error` after the retry classifier checks for more specific timeout,
context-overflow, dependency, and provider signals.
Provider/model prompt or content-policy rejections are classified as
`prompt_rejection` before the validation fallback, with terminal event details
kept metadata-only.
Caller-driven cancellation is classified as `cancelled`, not `unknown`, so
pilot failure-class distribution can separate intentional stops from defects.

## Quick example (async)

```python
async with agent.asession() as session:
    text, _ = await session.arun_turn(
        lambda: acall_model(prompt),
        model="model-a",
        timeout=30.0,
    )
```

Same parameters; `coro_factory` instead of `fn`. See
[`AsyncOrchestrationSession`](../api/orchestrator.md).

## Tool calls: timeouts, replay, and arguments

`run_tool` / `arun_tool` accept three optional keywords:

```python
session.run_tool(
    "charge",
    lambda: payments.charge(order),
    args={"order": order.id},       # visible to hooks and loop detection
    timeout=10.0,                   # raises ToolTimeoutError
    idempotency_key=f"charge:{order.id}",  # replayed on a re-run
)
```

- `timeout` cancels an async tool. A sync tool runs on a worker thread that Python
  cannot kill, so it finishes in the background: a tool whose side effects must
  not outlive the deadline has to enforce its own timeout too.
- `idempotency_key` needs a `saver` and a `thread_id`. Permission and governance
  checks still run on a replay, so revoking a permission takes effect immediately.
  See [Durability](durability.md#tool-calls) for the exact guarantee.

`arun_parallel_tools` only supplies the concurrency. To keep the checks, have each
factory go through `arun_tool`:

```python
await session.arun_parallel_tools([
    lambda: session.arun_tool("search", search_factory, args={"q": q}),
    lambda: session.arun_tool("fetch", fetch_factory, args={"url": url}),
])
```

## Falling back to another provider

With a `provider_router` configured, `run_turn_with_fallback` /
`arun_turn_with_fallback` retry a failed turn on the next model the router offers:

```python
agent = AgentSession(
    role="writer",
    phase="draft",
    provider_router=StaticProviderRouter(("model-a", "model-b")),
)
with agent.session() as session:
    text, usage, used_model = session.run_turn_with_fallback(
        lambda model: call_model(model, prompt),
        model="model-a",
    )
```

Each attempt is a full turn, so it counts towards `max_iterations` and governance.
By default only `PROVIDER_FAILURE` and `LLM_TIMEOUT` switch provider; pass
`failover_on` to change that. Runtime limits, permission denials, and an open
circuit always propagate. A circuit breaker on the session is shared by every
model, so it should guard something they have in common, such as a gateway.

## Handing off to another role

`handoff_to` reserves a worker for the target role. Pass the returned `Handoff` to
the target's session so it runs on that worker instead of leaving it
`INITIALIZING`:

```python
handoff = session.handoff_to("reviewer", "draft ready")
reviewer = AgentSession(role="reviewer", phase="draft", registry=agent.registry)
with reviewer.session(handoff=handoff) as review:
    ...
```

## When to use this

- **You want a single object to compose with**: the orchestrator
  threads worker lifecycle, breaker state, retry classification,
  permissions, guardrails, and cost accounting through one session.
- **You need tool-call observability**: successful `run_tool` /
  `arun_tool` calls emit `agent.tool_called` and
  `agent.tool_completed` events without copying the tool result into
  event data. Tool body exceptions emit `agent.failed` with
  `failure_class="tool_error"` and only the tool name in event data.
  Permission denials and guardrail blocks emit `agent.blocked` with
  metadata-only payloads; if they escape the session context, the
  terminal failure class remains `permission_denied` or
  `guardrail_violation`.
- **You're stitching multiple primitives manually**: stop, use
  `AgentSession` instead.
- **You need agent-level handoffs**: `session.handoff_to(target_role, ...)`
  finalizes the source worker and registers a new one under the same
  project_id. Use `session.ahandoff_to(target_role, ...)` when async
  handoff hooks must run.

## When *not* to use this

- **You need just one primitive** (only cost tracking, only a
  circuit breaker): import it directly. The orchestrator is a
  convenience layer, not a required entry point.
- **You need true durable execution** (workflows that survive process
  restart): the orchestrator is in-memory. Use the checkpointing
  primitives or an external coordinator for longer-lived workflows.
- **You're orchestrating across machines**: this is a single-process
  primitive. Run one per machine and coordinate externally.

## Anti-patterns

- **One session factory reused across unrelated agents.** Build
  a new `AgentSession(role=..., phase=..., ...)` per logical agent;
  pass the same `registry` and `event_sink` to keep observability
  joined.
- **Swallowing `BudgetExceededError` instead of acting on it.** The
  error carries `budget_usd` and `current_cost_usd`; route it to
  human review or a cheaper model rather than re-running blindly.
- **Wrapping `session.run_turn` in your own retry loop.** Recovery is
  already attempted once via `attempt_recovery`. Add caller-level
  retry only if you need recipes the runtime doesn't ship.

## Tuning the knobs

| Parameter | Default | When to raise | When to lower |
|---|---|---|---|
| `max_iterations` | 25 | Long planning loops with many small turns | Cheap, latency-sensitive paths |
| `budget_usd` | None | Per-session caps for production | Disable for batch / offline runs |
| `enforce_budget` | False | Production — raise on breach | Dev — log and continue |
| `timeout` (per turn) | None | Model API isn't reliable; needs a hard ceiling | Tools that genuinely take minutes |
| `event_sink` | `NoopEventSink` | Production observability | Tests, throwaway scripts |

## See also

- [`CircuitBreaker`](circuit-breaker.md) — breaker semantics.
- [`Retry Policy`](retry.md) — recovery recipes invoked on failure.
- [`Permissions`](permissions.md) — tool authorization.
- [`Usage Tracking`](usage-tracking.md) — cost computation.
- [`Policy Engine`](policy.md) — declarative phase actions.
- API reference: [Orchestrator](../api/orchestrator.md)
