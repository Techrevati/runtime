# Orchestrator

Author: Techrevati doo

`AgentSession` and `OrchestrationSession` (sync) /
`AsyncOrchestrationSession` (async) wire the other primitives into a
single execution loop. Use them when you want lifecycle tracking,
retry classification, circuit-breaker protection, permission gating,
guardrails, max-iterations cap, telemetry sinks, and cost accounting in
one place.

> **Naming note.** `AgentSession` is the canonical session factory.
> `Orchestrator` is kept as a deprecated compatibility alias through
> the 0.3.x line. Use `session.handoff_to(...)` or async
> `session.ahandoff_to(...)` to connect multiple sessions.

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
