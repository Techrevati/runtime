# End-to-End Tutorial — a tiny agent in 30 lines

This tutorial wires every primitive together into a single working
agent loop: pricing → orchestrator → permissions → circuit breaker →
policy → guardrail → tool gating → budget enforcement → handoff →
session summary. By the end you'll know which primitive does what,
and where to plug your real model and tool implementations.

The complete file lives at [`examples/tiny_agent.py`](https://github.com/Techrevati/runtime/blob/main/examples/tiny_agent.py) — clone the
repo and run it with `python -m examples.tiny_agent`.

## 1. Register pricing

Every cost calculation requires registered pricing. The bundled
`pricing.json` is intentionally empty so you control which models the
runtime knows about and what they cost.

```python
from techrevati.runtime import ModelPricing, register_pricing

register_pricing(
    "your-model",
    ModelPricing(input_per_million=3.0, output_per_million=15.0),
)
```

Without this step, `record_turn` logs a one-time warning per model and
all costs come out as `$0.00`.

## 2. Compose the orchestrator

The orchestrator is the factory. Each session you open inherits the
breakers, permissions, policy, guardrails, sinks, and budget you wire
in here.

```python
from techrevati.runtime import (
    CircuitBreaker, Orchestrator, PermissionEnforcer, PermissionMode,
    PermissionPolicy, RingBufferEventSink, RolePermissionConfig,
)

breaker = CircuitBreaker("model-api", failure_threshold=3, recovery_timeout_seconds=30.0)

permissions = PermissionEnforcer(PermissionPolicy(
    role_configs={
        "writer": RolePermissionConfig("writer", PermissionMode.READ_ONLY),
    },
    tool_requirements={"write_db": PermissionMode.FULL_ACCESS},
))

events = RingBufferEventSink()

orch = Orchestrator(
    role="writer",
    phase="draft",
    project_id=42,
    circuit_breaker=breaker,
    permissions=permissions,
    event_sink=events,
    budget_usd=0.50,
    enforce_budget=True,
    max_iterations=5,
)
```

What each knob does:

- **`circuit_breaker`** — opens after 3 consecutive model failures.
- **`permissions`** — the writer role can use read-only tools but not `write_db`.
- **`event_sink`** — buffers up to 1000 events in memory for inspection.
- **`budget_usd` + `enforce_budget=True`** — `run_turn` raises `BudgetExceededError` once the cumulative cost exceeds 50¢.
- **`max_iterations=5`** — kills runaway loops after 5 turns.

## 3. Run a turn

`run_turn` accepts any thunk plus a usage snapshot. The usage feeds
the tracker; cost is computed against the pricing registered in step 1.

```python
from techrevati.runtime import UsageSnapshot

def call_model() -> str:
    return "draft text"  # replace with your SDK call

with orch.session() as session:
    text, usage = session.run_turn(
        call_model,
        model="your-model",
        usage=UsageSnapshot(input_tokens=5_000, output_tokens=1_200),
        timeout=30.0,
    )
```

If `call_model()` raises, the runtime classifies the exception into a
`FailureScenario`, attempts recovery, records a `recovery_attempted`
event, then re-raises so the caller decides what to do next.

## 4. Call a tool with gating

`run_tool` enforces the permission policy and (if configured) every
guardrail before the tool's body executes.

```python
def lookup_term() -> str:
    return "RAG"  # any read-only operation works here

with orch.session() as session:
    fact = session.run_tool("lookup_term", lookup_term)
```

Trying `session.run_tool("write_db", ...)` from a writer role raises
`PermissionDeniedError` — the body is never called.

## 5. Hand off to another agent

When the writer is done, hand off to an editor. The source worker
finalizes; a new worker is registered for the target role under the
same project_id.

```python
with orch.session() as writer_session:
    handoff = writer_session.handoff_to(
        "editor", reason="needs review", context={"draft": text},
    )

editor_orch = Orchestrator(role="editor", phase="draft", project_id=42, registry=orch.registry)
with editor_orch.session() as editor_session:
    review_text, _ = editor_session.run_turn(
        lambda: "polished",
        model="your-model",
        usage=UsageSnapshot(input_tokens=2_000, output_tokens=400),
    )
```

Reusing the same `registry` makes the editor session visible to
`orch.registry.list_active()` so observability tools can see both
agents on the same project.

## 6. Evaluate policy and gate

A `PolicyEngine` lets you trigger declarative actions (advance phase,
abort, escalate) based on the session's current state. A `QualityGate`
records pass/fail events against an observed quality level.

```python
from techrevati.runtime import (
    PolicyAction, PolicyActionData, PolicyEngine, PolicyRule,
    QualityGate, QualityLevel,
)
from techrevati.runtime.policy_engine import And, PhaseCompleted, QualityAt

advance = PolicyRule(
    name="advance-on-quality",
    condition=And([PhaseCompleted(), QualityAt(QualityLevel.STANDARD)]),
    actions=[PolicyActionData(PolicyAction.ADVANCE_PHASE)],
    priority=10,
)
orch_with_policy = Orchestrator(
    role="writer",
    phase="draft",
    policy_engine=PolicyEngine([advance]),
    quality_gate=QualityGate(QualityLevel.STANDARD),
)
```

Inside the session, `session.evaluate_gate(observed_level)` and
`session.evaluate_policy(...)` produce events and action lists.
`elapsed_seconds` is auto-computed from session start, so `TimedOut`
conditions work without bookkeeping in the caller.

## 7. Read the summary

```python
import json
print(json.dumps(orch.event_sink.events[-3:], indent=2, default=str))
print(orch.tracker.format_cost())  # e.g. "$0.0156"
```

`session.summary()` returns a JSON-serializable snapshot of the
worker, usage, per-model cost, recovery events, and lifecycle events.

## What you skipped

- **Async path** — replace `with orch.session()` with `async with orch.asession()`, `run_turn` → `arun_turn`, `run_tool` → `arun_tool`. Same parameters.
- **OpenTelemetry** — `pip install 'techrevati-runtime[otel]'` and wire `OpenTelemetrySink` as the `event_sink`. See the [OTel API reference](../api/otel.md).
- **Guardrails** — pass `Orchestrator(guardrails=[...])` to gate tool inputs and outputs.

## Anti-patterns

- **Wrapping every primitive in your own helpers** — primitives are already small; layering thins the value of inspecting `session.events`.
- **Sharing one `CircuitBreaker` across unrelated downstreams** — the breaker counts failures globally. Use one per downstream.
- **Long-running sessions with no `usage_sink`** — `tracker.turns` grows without bound. Plug `RingBufferUsageSink` or your own.
