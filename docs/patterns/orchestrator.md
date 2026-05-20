# Orchestrator

`Orchestrator` and `OrchestrationSession` (sync) /
`AsyncOrchestrationSession` (async) wire the other primitives into a
single execution loop. Use them when you want lifecycle tracking,
retry classification, circuit-breaker protection, permission gating,
guardrails, max-iterations cap, OTel sinks, and cost accounting in
one place.

> **Naming note (be careful here).** In the broader 2026 agent
> literature *Anthropic's "Building Effective Agents"*, OpenAI Agents
> SDK), **"orchestrator-workers"** is a *delegation* pattern where one
> LLM dynamically dispatches subtasks to worker LLMs. Our
> `Orchestrator` is a **session wrapper** for a single agent. You
> implement the Anthropic-style pattern *on top* of our primitives by
> calling `session.handoff_to(...)` between sessions. The
> `Orchestrator` class is being kept for backward compatibility;
> `AgentSession` is the forward-looking name, becoming canonical
> in 0.2.0.

## Quick example (sync)

```python
from techrevati.runtime import (
    Orchestrator, UsageSnapshot, CircuitBreaker,
    register_pricing, ModelPricing,
)

register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))

orch = Orchestrator(
    role="writer",
    phase="draft",
    project_id=42,
    circuit_breaker=CircuitBreaker("model-api", failure_threshold=5),
    budget_usd=10.0,
    enforce_budget=True,
    max_iterations=25,
)

with orch.session() as session:
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
On `asyncio.CancelledError`, to `CANCELLED`.

## Quick example (async)

```python
async with orch.asession() as session:
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
- **You're stitching multiple primitives manually**: stop, use
  `Orchestrator` instead.
- **You need agent-level handoffs**: `session.handoff_to(target_role, ...)`
  finalizes the source worker and registers a new one under the same
  project_id — Anthropic's orchestrator-workers pattern, built up
  from our primitives.

## When *not* to use this

- **You need just one primitive** (only cost tracking, only a
  circuit breaker): import it directly. The orchestrator is a
  convenience layer, not a required entry point.
- **You need true durable execution** (workflows that survive process
  restart): the orchestrator is in-memory. Pair with Temporal / dbos.
  A pluggable checkpointer is on the 0.2.0 roadmap.
- **You're orchestrating across machines**: this is a single-process
  primitive. Run one per machine and coordinate externally.

## Anti-patterns

- **One orchestrator instance reused across unrelated agents.** Build
  a new `Orchestrator(role=..., phase=..., ...)` per logical agent;
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
