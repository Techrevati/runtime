# Orchestrator

`Orchestrator` and `OrchestrationSession` wire the other primitives into a single execution loop. Use it when you want lifecycle tracking, retry classification, circuit-breaker protection, permission gating, and cost accounting in one place.

## Quick example

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
)

with orch.session() as session:
    result, usage = session.run_turn(
        lambda: call_model(prompt),
        model="model-a",
        usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
    )

print(session.summary())
```

Entering the session transitions the worker through `INITIALIZING → RUNNING`. On clean exit, to `COMPLETED`. On exception, to `FAILED` with the error classified into an `AgentFailureClass`.

## Construction

```python
Orchestrator(
    role: str,
    phase: str,
    project_id: int | None = None,
    registry: AgentRegistry = AgentRegistry(),
    permissions: PermissionEnforcer | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    policy_engine: PolicyEngine | None = None,
    quality_gate: QualityGate | None = None,
    budget_usd: float | None = None,
)
```

Every dependency is optional. Pass only what you use; the session degrades gracefully (e.g. no `permissions` means `run_tool` always allows).

## Session methods

| Method | Purpose |
|---|---|
| `run_turn(fn, model="", usage=..., estimate_usage=...)` | Execute one model call wrapped in circuit breaker + recovery + usage recording |
| `run_tool(tool_name, fn)` | Execute a tool, gated by `permissions` |
| `evaluate_gate(observed)` | Compare an observed `QualityLevel` to the configured `QualityGate`; emits an event |
| `evaluate_policy(...)` | Run the configured `PolicyEngine` against current context |
| `complete(detail=None)` | Mark worker COMPLETED |
| `fail(detail, failure_class=...)` | Mark worker FAILED with a typed failure class |
| `summary()` | Dict with worker, usage, per-model cost, recovery events, and emitted agent events |

## When *not* to use it

If you need only one primitive (just cost tracking, just a circuit breaker), import that primitive directly. The Orchestrator is the convenience layer, not a required entry point.
