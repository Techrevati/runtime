# Migrating from 0.0.x to 0.1.0

0.1.0 is a beta release. Most existing 0.0.x calls keep working, but a
handful of changes are worth knowing before you bump the pin. This page
lists every behavior change in one place; pair it with the
[CHANGELOG](changelog.md) for the full feature list.

## TL;DR

```diff
- pip install techrevati-runtime==0.0.1
+ pip install techrevati-runtime==0.1.0
+ # Optional: pip install 'techrevati-runtime[otel]==0.1.0'
```

Then audit your code against the sections below. If none of them
apply, you're done — the rest is additive.

## Required action items

### 1. `evaluate_policy(elapsed_seconds=0.0)` no longer means "always zero"

In 0.0.x, `elapsed_seconds` defaulted to `0.0` and `TimedOut`
conditions never fired unless the caller computed elapsed manually.

In 0.1.0, the parameter type is `float | None` (default `None`). When
omitted, the session auto-computes elapsed from session start.
Time-based policy rules now finally fire.

**If your code passed `0.0` explicitly to silence `TimedOut` rules:**
that behavior is gone. The signature change means `0.0` is now a real
value telling the engine zero seconds have passed. Either:

- Pass the actual elapsed value you want, or
- Use the default (`evaluate_policy(...)` with no `elapsed_seconds`)
  to get auto-computation.

```python
# Before (0.0.x) — TimedOut never fires:
actions = session.evaluate_policy(elapsed_seconds=0.0)

# After (0.1.0) — TimedOut fires when elapsed > rule.seconds:
actions = session.evaluate_policy()  # auto from session start
```

### 2. Default jitter algorithm changed

`backoff_delay()` defaulted to a custom "25% additive jitter" formula
in 0.0.x. In 0.1.0 the default is `"decorrelated"` (Marc Brooker /
AWS Builders' Library, the fastest of the documented algorithms).

If you depend on the old wave shape, pass `jitter="equal"` for the
closest analogue, or `jitter="none"` for pure exponential.

```python
# Before (0.0.x):
delay = backoff_delay(attempt=2)  # 4 + uniform(0, 1) ≈ [4.0, 5.0]

# After (0.1.0):
delay = backoff_delay(attempt=2)  # decorrelated; uniform([base, prev*3]) capped
delay = backoff_delay(attempt=2, jitter="equal")  # legacy-ish shape
```

`jitter=True` (bool, the 0.0.x default) still works — it now maps to
`"full"` jitter. `jitter=False` maps to `"none"`.

### 3. `time.time()` → `time.monotonic()` inside `CircuitBreaker`

Recovery-window calculations now use `time.monotonic`. If you were
faking time with `freezegun` or by monkey-patching `time.time`, those
tricks won't affect breaker behavior anymore. Use the new
`CircuitBreaker(clock=...)` parameter to inject a deterministic clock:

```python
# Recommended pattern for deterministic tests:
class ManualClock:
    def __init__(self, t: float = 0.0): self._t = t
    def __call__(self) -> float: return self._t
    def advance(self, dt: float): self._t += dt

clock = ManualClock()
cb = CircuitBreaker("svc", recovery_timeout_seconds=60.0, clock=clock)
# ... trip the breaker ...
clock.advance(70.0)  # now half-open
```

## Likely-relevant additions

These are new — they don't break anything, but they probably affect
how you build:

### `Orchestrator(enforce_budget=True)`

In 0.0.x, `budget_usd` was monitoring-only — a breach logged an event
and the session continued. In 0.1.0 you can opt in to enforcement:

```python
orch = Orchestrator(
    role="writer", phase="draft",
    budget_usd=10.0,
    enforce_budget=True,   # NEW: raises BudgetExceededError after the breach
)
```

If you've been relying on the "log and continue" behavior (e.g.
deferring a decision to a downstream system), leave `enforce_budget`
at its default `False`.

### `Orchestrator(max_iterations=N)`

Default is `25`. Set to a smaller number for cheap latency-sensitive
paths or larger for long planning loops. `MaxIterationsExceededError`
is raised before the (max+1)th turn invocation.

### `Orchestrator(guardrails=[...])`

Guardrails run automatically around `run_tool` / `arun_tool`. If you
have content checks today wrapped around your tool implementations
("contains_pii", "matches_schema"), move them into `Guardrail`
implementations to get consistent enforcement, error messages, and
observability events.

### Async path (`asession()` / `arun_turn` / `arun_tool`)

The package docstring in 0.0.x claimed an async runtime that didn't
exist. 0.1.0 ships it for real. Migration is mostly mechanical:

```python
# Before (sync):
with orch.session() as session:
    text, _ = session.run_turn(lambda: call_model(p), model="m", usage=u)
    fact = session.run_tool("lookup", lambda: tool_fn(arg))

# After (async):
async with orch.asession() as session:
    text, _ = await session.arun_turn(lambda: acall_model(p), model="m", usage=u)
    fact = await session.arun_tool("lookup", lambda: atool_fn(arg))
```

You can keep using the sync path — they're equal-status siblings.

### `AgentSession` alias for `Orchestrator`

`AgentSession = Orchestrator` is exported. 0.2.0 will promote
`AgentSession` to the canonical name and keep `Orchestrator` as a
deprecation alias. Adopt the new name in new code; existing code keeps
working.

### Sinks and OpenTelemetry

`Orchestrator(event_sink=..., usage_sink=...)` accepts any
`EventSink` / `UsageSink` implementation. Install
`techrevati-runtime[otel]` and wire `OpenTelemetrySink` if you want
your APM dashboard to surface the runtime alongside OpenAI Agents
SDK traces:

```python
from techrevati.runtime.otel import OpenTelemetrySink, OpenTelemetryUsageSink

orch = Orchestrator(
    role="writer", phase="draft",
    event_sink=OpenTelemetrySink(agent_id="writer-001"),
    usage_sink=OpenTelemetryUsageSink(),
)
```

Spans follow the [OpenTelemetry GenAI agent spans semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/).

## Not changing in 0.1.0

- Public types: `UsageSnapshot`, `ModelPricing`, `RecoveryContext`,
  `PolicyEngine`, `QualityGate`, `PermissionEnforcer`,
  `PermissionPolicy`, `RolePermissionConfig`.
- Module paths: nothing was moved.
- Wheel structure: `techrevati.runtime` is still the import root.
- Behavior of `register_pricing` and `load_pricing_from_file`.
- The deny-first ordering of `PermissionEnforcer`.

## Coming in 0.2.0 (forward-looking)

- `Orchestrator` → `AgentSession` rename promoted to canonical (with
  `DeprecationWarning` on the old name).
- `CheckpointSaver` Protocol + `SqliteSaver` reference implementation
  for pluggable persistence.
- `TokenBucket` rate-limiter primitive.
- Sigstore signing on release artifacts.

If you build against any of these in 0.1.0 today, expect their final
shape to land in 0.2.0.
