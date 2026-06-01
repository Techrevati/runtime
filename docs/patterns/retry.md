# Retry Policy

Failure classification and recipe lookup. The caller decides whether and how to retry; this module provides the typed scenario, recipe steps, and attempt accounting.

## Usage

```python
from techrevati.runtime import (
    classify_exception, attempt_recovery, RecoveryContext, RecoveryStep,
)

ctx = RecoveryContext()

try:
    response = call_model(prompt)
except Exception as exc:
    scenario = classify_exception(exc)
    result = attempt_recovery(scenario, ctx)

    if result.outcome == "recovered":
        if RecoveryStep.RETRY_WITH_SMALLER_CONTEXT.value in result.recovered_steps:
            prompt = shrink(prompt)
        if RecoveryStep.SWITCH_PROVIDER.value in result.recovered_steps:
            client = next_provider_client(client)
        response = call_model(prompt)
    elif result.outcome == "escalation_required":
        raise RuntimeError(result.reason) from exc
```

`RecoveryContext` is per logical task. Reuse the same instance across retries so attempt budgets carry over.

## Failure scenarios and recipes

| Scenario | Default recipe (max attempts) |
|---|---|
| `LLM_TIMEOUT` | RETRY_WITH_BACKOFF (2) |
| `LLM_ERROR` | RETRY_WITH_BACKOFF → SWITCH_PROVIDER (2) |
| `TOOL_EXECUTION_ERROR` | RESTART_AGENT (1) |
| `CONTEXT_OVERFLOW` | RETRY_WITH_SMALLER_CONTEXT → REDUCE_TOOL_SET (1) |
| `DEPENDENCY_TIMEOUT` | RETRY_WITH_BACKOFF (1) |
| `MEMORY_CORRUPTION` | CLEAR_MEMORY_CACHE → RESTART_AGENT (1) |
| `PROVIDER_FAILURE` | SWITCH_PROVIDER → RETRY_WITH_BACKOFF (2) |

Look up a recipe with `recipe_for(scenario)`. Modify the registry by editing the module-level `_RECIPES` dict at startup if your fault model is different.

## Classification boundary

`classify_exception()` first checks well-known exception types, including
timeouts, connection errors, JSON decode errors, and local `OSError` failures.
Wrapped causes are inspected too, so a `RuntimeError` raised from a disk,
filesystem, or dependency I/O error still maps to `DEPENDENCY_TIMEOUT` instead
of being reported as an LLM failure.

String matching covers common provider and dependency messages. Database,
SQLite, disk, filesystem, read-only, and no-space-left errors are classified as
dependency failures so terminal audit events can distinguish runtime
infrastructure problems from model failures.

## Escalation policies

- `EscalationPolicy.ALERT_HUMAN`
- `EscalationPolicy.LOG_AND_CONTINUE`
- `EscalationPolicy.ABORT`

## Helpers

- `backoff_delay(attempt, base=2.0, jitter=True)` — exponential delay with optional jitter
- `next_provider(available_providers, current)` — pick the next fallback
- `smaller_context_budget(current_chars, reduction=0.75)` — shrink a context budget

## RecoveryResult

```python
@dataclass
class RecoveryResult:
    outcome: Literal["recovered", "partial_recovery", "escalation_required"]
    steps_taken: int
    recovered_steps: list[str]
    remaining_steps: list[str]
    reason: str
```

Inspect `recovered_steps` to apply the recipe before retrying. Inspect `ctx.events` for the structured event log.
