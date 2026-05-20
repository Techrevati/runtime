# Governance Plane

`GovernancePlane` is the runtime's last line of defense: hard-stop
limits enforced *outside* agent code so the agent cannot bypass them
via recovery. When a limit configured with `on_breach="terminate"` is
exceeded, the orchestrator raises `GovernanceBreachError`, which the
session marks as `FAILED` and re-raises **without** going through the
failure classifier or the recovery loop.

This is the technical primitive auditors expect for EU AI Act
deployments — Article 14 (human oversight via stopping conditions),
Article 15 (robustness / fail-safes), and Article 26 (deployer
monitoring + reporting). The full article-by-article compliance mapping
ships in 0.3.0 Sprint 6 (`docs/compliance/`).

## When to use this

- **You ship to EU customers** and any user-deployed system would meet
  the Annex III "high-risk" definition. Article 9 risk management,
  Article 12 record-keeping, and Article 26 deployer-side monitoring
  all want a runtime kill-switch.
- **Cost is dollars per token, not milliseconds per request.** A
  budget cap that the agent could in principle catch + recover from is
  not a hard cap. `MaxBudgetLimit(on_breach="terminate")` is.
- **The agent runs unattended and must stop on its own.** Production
  multi-step agent loops can drift; a 25-turn iteration cap + a
  consecutive-failure cap rules out the canonical runaway-loop
  failure mode.
- **You need a rollout signal before flipping a knob to hard-stop.**
  Use `on_breach="alert"` first; measure breach rates from the
  `governance.alert` events; flip to `"terminate"` when you trust the
  threshold.

## When NOT to use this

- For *recoverable* token / cost ceilings inside one session that the
  agent is allowed to react to. Use [`UsageLimits`](usage-tracking.md)
  instead — its `UsageLimitExceededError` is catchable and recovery
  flows can respond.
- For per-tool authorization. Use [`PermissionEnforcer`](permissions.md).
- For pattern blocking on tool inputs or outputs. Use the
  built-in `PatternGuardrail` / `PromptInjectionGuardrail` (or a
  custom `Guardrail`) — see [Permissions](permissions.md) and
  [API: Guardrails](../api/guardrails.md).

## Quickstart

```python
from techrevati.runtime import (
    AgentSession,
    GovernancePlane,
    MaxBudgetLimit,
    MaxConsecutiveFailuresLimit,
    MaxIterationsLimit,
    MaxToolCallsLimit,
)

plane = GovernancePlane(
    limits=(
        MaxIterationsLimit(value=25, on_breach="terminate"),
        MaxBudgetLimit(value=5.00, on_breach="terminate"),
        MaxConsecutiveFailuresLimit(value=3, on_breach="terminate"),
        MaxToolCallsLimit(value=100, on_breach="alert"),
    ),
)

session = AgentSession(role="writer", phase="draft", governance=plane)
```

The orchestrator ticks the plane's counters at three points:

- **Pre-turn** — `record_turn_start()` and `enforce()`. The iteration
  cap fires here.
- **Pre-tool** — `record_tool_call()` and `enforce()`. The tool-call
  cap fires here.
- **Post-turn** — `record_success()` or `record_failure()`,
  `record_cost(cost_delta)`, and `enforce()`. The budget and
  consecutive-failures caps fire here.

## The four built-in limits

### `MaxIterationsLimit`

Caps total turns in the session. Distinct from
`AgentSession.max_iterations` — that one raises a recoverable
`MaxIterationsExceededError`; this one is terminal.

```python
MaxIterationsLimit(value=25, on_breach="terminate")
```

### `MaxBudgetLimit`

Caps cumulative cost in USD. Distinct from `UsageLimits.cost_usd_max`
— that one is recoverable; this one is terminal.

```python
MaxBudgetLimit(value=5.00, on_breach="terminate")
```

### `MaxConsecutiveFailuresLimit`

Counts consecutive failures. A single successful turn resets the
counter to zero. Catches "the agent retries the same broken thing
forever" failure modes that per-step retry budgets alone do not.

```python
MaxConsecutiveFailuresLimit(value=3, on_breach="terminate")
```

### `MaxToolCallsLimit`

Caps total tool invocations in the session. Distinct from
`UsageLimits.tool_calls_max` only in being terminal.

```python
MaxToolCallsLimit(value=100, on_breach="alert")
```

## `on_breach` modes

| Mode | Behavior |
|---|---|
| `"terminate"` (default) | Raises `GovernanceBreachError`. Worker → `FAILED`. Recovery loop is **NOT** invoked. |
| `"alert"` | Emits a `governance.alert` event on every breached evaluation. Session continues. |

Rolling out a new limit safely: deploy with `"alert"` for 1–2 weeks,
observe the `governance.alert` event rate, then flip to `"terminate"`.

## Event surface

Two new `AgentEventName` values surface in 0.3.0:

- `governance.breach` — emitted **before** `GovernanceBreachError`
  raises so downstream sinks see the breach in the audit log even when
  the exception propagates past them.
- `governance.alert` — emitted once per evaluation per breached
  alert-mode limit.

Both carry `data = {limit_name, observed, ceiling, scope}` for sink
serialization.

## Composing with `UsageLimits`

These two primitives are not redundant — they sit at different layers.

```python
sess = AgentSession(
    role="writer",
    phase="draft",
    # Soft cap: agent code can catch UsageLimitExceededError and react.
    usage_limits=UsageLimits(total_tokens_max=200_000),
    # Hard cap: governance breach terminates the session regardless.
    governance=GovernancePlane(
        limits=(MaxBudgetLimit(value=10.00, on_breach="terminate"),),
    ),
)
```

A common pattern is: `usage_limits` cap at 80% of the budget, `governance`
hard-stop at 100%. The agent gets a recoverable warning before the
session dies.

## Tuning the knobs

| Knob | Reasonable range | Notes |
|---|---|---|
| `MaxIterationsLimit.value` | 10–50 for production loops | Same default as OpenAI Agents SDK. |
| `MaxBudgetLimit.value` | per-customer / per-session limit | Pair with `UsageLimits.cost_usd_max` at 80%. |
| `MaxConsecutiveFailuresLimit.value` | 2–5 | Below 2 is twitchy; above 5 hides real reliability bugs. |
| `MaxToolCallsLimit.value` | 5×–10× expected | Useful as an alert before flipping to terminate. |
| `on_breach="alert"` | Always start here for new limits | Measure first, terminate second. |

## Anti-patterns

- **Catching `GovernanceBreachError` and retrying.** Don't. The point
  of the plane is that the agent cannot bypass it. If you want
  recoverable behavior, use `UsageLimits` instead.
- **Putting business logic in `GovernanceState.record_*`.** The state
  object is a counter; do not subclass it to fire side effects on
  every tick. Add a custom `EventSink` for that.
- **One plane per turn.** Construct the plane once and pass it to
  `AgentSession`; do not create a new plane on each turn — counters
  reset.
- **Mixing `"alert"` and `"terminate"` randomly across limits.** Pick a
  rollout phase per limit, document it, and don't half-migrate.

## Sources

- Waxell — *AI Agent Circuit Breakers: The Reliability Pattern Production
  Teams Are Missing* — [https://dev.to/waxell/ai-agent-circuit-breakers-...](https://dev.to/waxell/ai-agent-circuit-breakers-the-reliability-pattern-production-teams-are-missing-5bpg)
- DZone — *Engineering Hard-Stop Safety Into Autonomous Agent
  Workflows* — [https://dzone.com/articles/algorithmic-circuit-breakers-agent-safety](https://dzone.com/articles/algorithmic-circuit-breakers-agent-safety)
- EU AI Act Articles 9, 12, 14, 15, 26 — [artificialintelligenceact.eu](https://artificialintelligenceact.eu/section/3-2/)
