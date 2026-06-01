# Runtime

Author: Techrevati doo

Runtime primitives for multi-step LLM agent loops: sync and async sessions,
retry classification, circuit-breaker protection, usage tracking, optional
budget enforcement, role-based tool gating, guardrails, handoffs, policy
evaluation, checkpointing, rate limiting, streaming, hooks, and telemetry
integration.

The package is currently `0.3.0rc1`. This is a release candidate; the `0.x`
API surface is still unstable, so pin exact versions when you depend on a
specific behavior.

```bash
pip install techrevati-runtime
pip install 'techrevati-runtime[otel]'
```

## Quick Start

```python
from techrevati.runtime import (
    AgentSession,
    ModelPricing,
    UsageSnapshot,
    register_pricing,
)

register_pricing(
    "model-a",
    ModelPricing(input_per_million=3.0, output_per_million=15.0),
)

agent = AgentSession(
    role="writer",
    phase="draft",
    project_id=1,
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

The session moves through `INITIALIZING -> RUNNING -> COMPLETED`, classifies
exceptions into typed failure scenarios, attempts recovery once, enforces the
configured budget, gates tool calls behind permissions and guardrails, and emits
structured events to the configured sinks.

For async code, use `async with`, `asession()`, and `arun_turn()` with the same
parameters. Cancellation transitions the worker to `CANCELLED`.

## Design Goals

- Zero runtime dependencies; optional extras are opt-in.
- Type-safe public API with `py.typed`.
- Composable primitives that work standalone or through `AgentSession`.
- Thread-safe sync paths and async-safe async paths.
- Caller-owned configuration for pricing, thresholds, roles, sinks, and policy.

## Main Primitives

| Module | Provides |
|---|---|
| `orchestrator` | `AgentSession`, sync and async sessions, `Orchestrator` compatibility alias |
| `circuit_breaker` | Sync and async circuit breakers |
| `retry_policy` | Failure classification and recovery recipes |
| `usage_tracking` | Usage snapshots, pricing registration, limits, budgets |
| `agent_lifecycle` | Worker registry and validated lifecycle transitions |
| `agent_events` | Typed lifecycle events |
| `permissions` | Deny-first role and tool authorization |
| `guardrails` | Pre-call and post-call content checks |
| `handoffs` | Agent-to-agent delegation records |
| `policy_engine` | Declarative policy conditions and actions |
| `checkpoint` | In-memory and SQLite checkpoint savers |
| `rate_limit` | Token buckets and rate limiters |
| `streaming` | Structured async stream events |
| `hooks` | Mutating lifecycle hook chain |
| `sinks` | Event and usage sink protocols |
| `persistence` | SQLite-backed durable sinks |
| `otel` | Optional telemetry sinks |

## Example: Async Handoff

```python
import asyncio

from techrevati.runtime import (
    AgentSession,
    AllowAllGuardrail,
    AsyncCircuitBreaker,
    UsageSnapshot,
)

cb = AsyncCircuitBreaker(
    "model-api",
    failure_threshold=3,
    recovery_timeout_seconds=30.0,
)

async def main():
    agent = AgentSession(
        role="writer",
        phase="draft",
        async_circuit_breaker=cb,
        guardrails=[AllowAllGuardrail()],
        max_iterations=10,
    )

    async with agent.asession() as session:
        text, _ = await session.arun_turn(
            lambda: acall_model(prompt),
            model="model-a",
            usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
            timeout=30.0,
        )
        handoff = session.handoff_to(
            "editor",
            reason="review",
            context={"draft": text},
        )
        print(f"handed off to {handoff.target_role}")

asyncio.run(main())
```

## Limits

- Pricing is caller-provided. Unknown models are tracked with zero cost and a
  warning.
- Budget enforcement is opt-in with `enforce_budget=True`.
- Permissions and guardrails are runtime gates, not process sandboxes.
- Durable execution is opt-in through a `CheckpointSaver` and stable
  `thread_id`.
- Default sinks are in-memory ring buffers; long-running sessions should plug in
  durable sinks.
- Circuit breaker state is per process.

## License

MIT. Copyright 2026 Techrevati doo. See `LICENSE`.
