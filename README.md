# techrevati-runtime

[![PyPI](https://img.shields.io/badge/pypi-techrevati--runtime-blue.svg)](https://pypi.org/project/techrevati-runtime/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Type Safe](https://img.shields.io/badge/py.typed-yes-brightgreen.svg)](https://peps.python.org/pep-0561/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](#design-goals)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Production runtime primitives for multi-step LLM agent loops — retry classification, circuit-breaker protection, per-model cost tracking, role-based tool gating, declarative policies, and a unifying `Orchestrator` session.

```bash
pip install techrevati-runtime
```

```python
from techrevati.runtime import Orchestrator, UsageSnapshot, register_pricing, ModelPricing

register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))

orch = Orchestrator(role="writer", phase="draft", project_id=1)
with orch.session() as session:
    result, usage = session.run_turn(
        lambda: call_model(prompt),
        model="model-a",
        usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
    )

print(session.summary())
```

The session above transitions the worker through INITIALIZING → RUNNING, classifies any exception that bubbles up into a typed failure scenario, records cost against the registered pricing, and emits structured events for observability — without you wiring any of it by hand.

## Design goals

- **Zero runtime dependencies.** Imports are stdlib only.
- **Type-safe.** `py.typed` marker shipped; clean under `mypy --strict`.
- **Composable.** Every primitive (`CircuitBreaker`, `RetryContext`, `QualityGate`, `PolicyEngine`, `UsageTracker`, `PermissionEnforcer`) is usable standalone. The `Orchestrator` is just the wiring.
- **Thread-safe.** Locks on every shared mutable state.
- **Configuration-free at the edges.** Pricing data is empty by default; phase thresholds are not hardcoded; permission roles are caller-defined. The runtime stays opinion-free about what your numbers mean.

## Primitives

| Module | Provides |
|---|---|
| `orchestrator` | `Orchestrator`, `OrchestrationSession` — one execution loop |
| `retry_policy` | `classify_exception`, `attempt_recovery`, `RecoveryContext` |
| `circuit_breaker` | `CircuitBreaker` (CLOSED/OPEN/HALF_OPEN) |
| `quality_gate` | `QualityLevel`, `QualityGate` |
| `usage_tracking` | `UsageTracker`, `register_pricing`, `load_pricing_from_file` |
| `agent_lifecycle` | `AgentRegistry`, `AgentWorker` with validated state transitions |
| `agent_events` | Typed lifecycle events + OpenTelemetry attribute bridge |
| `permissions` | Role × tool authorization, deny-first |
| `policy_engine` | Composable conditions (`And`/`Or`/`QualityAt`/...) and rule evaluator |

## Standalone snippets

### Failure classification + recovery recipe

```python
from techrevati.runtime import classify_exception, attempt_recovery, RecoveryContext

ctx = RecoveryContext()
try:
    response = call_model(prompt)
except Exception as exc:
    scenario = classify_exception(exc)
    result = attempt_recovery(scenario, ctx)
    if result.outcome == "recovered":
        # Apply suggested steps (e.g. RETRY_WITH_BACKOFF, SWITCH_PROVIDER) and retry.
        response = call_model(prompt)
    elif result.outcome == "escalation_required":
        notify_human(result.reason)
```

### Circuit breaker

```python
from techrevati.runtime import CircuitBreaker, CircuitOpenError

cb = CircuitBreaker("downstream", failure_threshold=5, recovery_timeout_seconds=60.0)
try:
    result = cb.call(fetch, url, timeout=10)
except CircuitOpenError:
    result = fallback()
```

### Cost tracking with caller-provided pricing

```python
from techrevati.runtime import (
    UsageTracker, UsageSnapshot, ModelPricing,
    register_pricing, load_pricing_from_file,
)

# Register pricing in code...
register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))
# ...or load a JSON file you control.
load_pricing_from_file("/etc/myorg/pricing.json")

tracker = UsageTracker()
tracker.record_turn("model-a", UsageSnapshot(input_tokens=5000, output_tokens=1200))
print(tracker.format_cost())
```

### Quality gate

```python
from techrevati.runtime import QualityGate, QualityLevel

gate = QualityGate(QualityLevel.STRICT)
outcome = gate.evaluate(observed_level)
if not outcome.satisfied:
    request_rework(outcome)
```

### Policy rules

```python
from techrevati.runtime import (
    PolicyEngine, PolicyRule, PolicyAction, PolicyActionData, PhaseContext,
)
from techrevati.runtime.policy_engine import And, PhaseCompleted, QualityAt
from techrevati.runtime import QualityLevel

advance = PolicyRule(
    name="advance-on-quality",
    condition=And([PhaseCompleted(), QualityAt(QualityLevel.STANDARD)]),
    actions=[PolicyActionData(PolicyAction.ADVANCE_PHASE)],
    priority=10,
)
engine = PolicyEngine([advance])

for action in engine.evaluate(PhaseContext(...)):
    dispatch(action)
```

## Status

`techrevati-runtime` is at version `0.0.0`. APIs are explicitly unstable. Async-first redesign is the next milestone (`0.1.0`). Issues and PRs welcome.

## License

MIT — copyright © 2026 [TechRevati doo](https://techrevati.com). See [LICENSE](LICENSE).
