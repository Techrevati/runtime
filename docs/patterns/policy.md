# Policy Engine

Declarative rule evaluation over a `PhaseContext`. Composable conditions, named rules, sorted by priority. The engine returns a flat list of `PolicyActionData` — the caller dispatches actions.

## Usage

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
    dispatch(action)
```

## Conditions

| Condition | Matches when |
|---|---|
| `QualityAt(level)` | `ctx.quality_level >= level` |
| `PhaseCompleted()` | `ctx.phase_completed` |
| `AgentFailed(role=None)` | Any role failed (or a specific one) |
| `GateBelow(threshold)` | `ctx.gate_score < threshold` |
| `RetryExhausted(scenario=None)` | Recovery retries exhausted |
| `TimedOut(seconds)` | `ctx.elapsed_seconds > seconds` |
| `AllAgentsComplete()` | `completed ∪ failed == all` |
| `CostExceeded(usd)` | `ctx.total_cost_usd > usd` |
| `And([...])` | All children match |
| `Or([...])` | Any child matches |

Subclass `PolicyCondition` and override `matches(ctx)` to add your own.

## Actions

`PolicyAction` is an enum the caller maps to behavior:

- `ADVANCE_PHASE`, `RETRY_AGENT`, `RETRY_PHASE`, `RECOVER_ONCE`, `ESCALATE`, `STORE_GATE_FEEDBACK`, `GENERATE_HANDOFF`, `NOTIFY`, `ABORT_PHASE`

Wrap in `PolicyActionData(action, params=dict)` to attach parameters.

## Engine semantics

- Rules are sorted by `priority` (lower fires first).
- *All* matching rules fire — there's no short-circuit on first match.
- The engine returns a flat list of action data; it does not dispatch.

```python
class PolicyEngine:
    def __init__(self, rules: list[PolicyRule]): ...
    def evaluate(ctx: PhaseContext) -> list[PolicyActionData]
    @property rules: list[PolicyRule]
```

Rules cannot be added after construction — pass the full list.
