# Getting Started

Author: Techrevati doo

## Install

```bash
pip install techrevati-runtime
```

Requires Python 3.11+. No runtime dependencies.

## The easy path: AgentSession

`AgentSession` opens a session with lifecycle, usage tracking, retry
classification, circuit-breaker protection, permission gating, and policy
evaluation already wired in.

```python
from techrevati.runtime import (
    AgentSession, UsageSnapshot, CircuitBreaker,
    register_pricing, ModelPricing,
)

# Pricing is empty by default. Register what you use.
register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))

agent = AgentSession(
    role="writer",
    phase="draft",
    project_id=42,
    circuit_breaker=CircuitBreaker("model-api", failure_threshold=5),
    budget_usd=10.0,
)

with agent.session() as session:
    result, usage = session.run_turn(
        lambda: call_model(prompt),
        model="model-a",
        usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
    )

print(session.summary())
```

On clean exit, the session transitions the worker to `COMPLETED`. On
exception, to `FAILED` with the error classified into an
`AgentFailureClass`.

## Primitives, standalone

Every primitive works without the session factory.

### Failure classification + retry recipe

```python
from techrevati.runtime import classify_exception, attempt_recovery, RecoveryContext

ctx = RecoveryContext()

try:
    response = call_model(prompt)
except Exception as exc:
    scenario = classify_exception(exc)
    result = attempt_recovery(scenario, ctx)
    if result.outcome == "recovered":
        # Apply recipe steps (e.g. RETRY_WITH_BACKOFF, SWITCH_PROVIDER) and retry.
        response = call_model(prompt)
    elif result.outcome == "escalation_required":
        notify_human(result.reason)
```

`result.recovered_steps` is the list of recipe steps the caller should
apply before retrying. The recipe registry is in `retry_policy.py`.

### Agent lifecycle

```python
from techrevati.runtime import AgentRegistry, AgentStatus

registry = AgentRegistry()
worker = registry.create(role="writer", phase="draft", project_id=42)

worker.transition(AgentStatus.INITIALIZING)
worker.transition(AgentStatus.RUNNING, detail="context_budget=8k")
worker.transition(AgentStatus.COMPLETED, detail="observed=STRICT")

for event in worker.events:
    print(f"{event.timestamp}: {event.status} - {event.detail}")
```

### Permissions

```python
from techrevati.runtime import (
    PermissionMode, RolePermissionConfig, PermissionPolicy, PermissionEnforcer,
)

policy = PermissionPolicy(
    role_configs={
        "writer": RolePermissionConfig(
            role="writer",
            mode=PermissionMode.FULL_ACCESS,
            denied_tools=["dangerous_tool"],
        ),
        "reader": RolePermissionConfig(role="reader", mode=PermissionMode.READ_ONLY),
    },
    tool_requirements={"dangerous_tool": PermissionMode.FULL_ACCESS},
)
enforcer = PermissionEnforcer(policy)

outcome = enforcer.check("writer", "any_tool")
if not outcome.allowed:
    raise RuntimeError(outcome.reason)
```

### Cost tracking

```python
from techrevati.runtime import (
    UsageTracker, UsageSnapshot, ModelPricing, register_pricing,
)

register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))

tracker = UsageTracker()
tracker.record_turn("model-a", UsageSnapshot(input_tokens=5000, output_tokens=1200))

print(tracker.format_cost())
print(tracker.per_model_summary())

if tracker.is_over_budget(budget_usd=10.0):
    raise RuntimeError("budget exceeded")
```

Unknown models fall back to zero pricing (treated as free). Override or
extend pricing at any time with `register_pricing` or `load_pricing_from_file`.

### Circuit breaker

```python
from techrevati.runtime import CircuitBreaker, CircuitOpenError

cb = CircuitBreaker("downstream", failure_threshold=5, recovery_timeout_seconds=60.0)

try:
    result = cb.call(fetch, url, timeout=10)
except CircuitOpenError:
    result = fallback()
```

### Policy engine

```python
from techrevati.runtime import (
    PolicyEngine, PolicyRule, PolicyAction, PolicyActionData,
    PhaseContext, QualityLevel,
)
from techrevati.runtime.policy_engine import And, PhaseCompleted, QualityAt

rule = PolicyRule(
    name="advance-on-quality",
    condition=And([PhaseCompleted(), QualityAt(QualityLevel.STANDARD)]),
    actions=[PolicyActionData(PolicyAction.ADVANCE_PHASE)],
    priority=10,
)
engine = PolicyEngine([rule])

ctx = PhaseContext(
    phase="draft",
    quality_level=QualityLevel.STRICT,
    phase_completed=True,
    completed_roles={"writer"},
    all_roles={"writer"},
)
for action in engine.evaluate(ctx):
    print(action.action.value, action.params)
```

Rules are passed at construction and sorted by `priority` (lower fires
first). All matching rules fire; the engine returns the flat list of
actions for the caller to dispatch.

### Quality gate

```python
from techrevati.runtime import QualityGate, QualityLevel

gate = QualityGate(QualityLevel.STRICT)
outcome = gate.evaluate(observed_level)
if outcome.satisfied:
    advance()
else:
    request_rework(outcome)
```

## Best practices

1. **Reuse one `RecoveryContext` per logical task.** Attempt budgets are
   per-context, not global.
2. **Register pricing once at startup.** `register_pricing` is thread-safe
   but adding pricing inside a hot path is wasted work.
3. **Use a circuit breaker per external dependency.** One breaker per host
   or per endpoint, not a global one.
4. **Make policy rules small and named.** Engine evaluation logs use
   `rule.name` — descriptive names make traces readable.
5. **Treat unknown models as free.** Tracking will record them with zero
   cost; alert if that's not expected.
