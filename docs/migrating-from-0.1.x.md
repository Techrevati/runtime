# Migrating from 0.1.x

0.2.0 is additive in most places, with two intentional changes you'll
need to react to. Existing 0.1.x calls keep working unless you depend
on the telemetry sink's wire format or on `Orchestrator` being the canonical
class name.

## Required action items

### 1. Prefer `AgentSession` over `Orchestrator`

The canonical class name is now `AgentSession`. `Orchestrator` is still
exported as a deprecated compatibility subclass and constructs the same
kind of session, but it will be removed no earlier than 0.4.0. Update
imports at your convenience:

```python
# Old (still works through 0.3.x):
from techrevati.runtime import Orchestrator
orch = Orchestrator(role="writer", phase="draft")

# New (recommended):
from techrevati.runtime import AgentSession
agent = AgentSession(role="writer", phase="draft")
```

The constructor signature is identical. `Orchestrator(...)` returns an
`AgentSession` subclass instance, so `isinstance(x, AgentSession)`
remains true. Prefer `AgentSession` for new code so upgrades do not
emit deprecation warnings.

### 2. Telemetry sink wire format: one-shot -> nested

The telemetry sink in 0.1.x emitted one independent span per
`AgentEvent`. In 0.2.0 it opens a long-lived parent span on
`AGENT_STARTED` / `PHASE_STARTED` and ends it on
`AGENT_COMPLETED` / `AGENT_FAILED` / `PHASE_COMPLETED`. Every other
event emits as a child of the open parent.

Practical implications:

- Your APM tool / dashboard now sees one root span per session with
  recovery, gate, and tool events nested under it (instead of dozens
  of unrelated roots). Tracing UIs render this as a real trace tree.
- `gen_ai.operation.name` on the parent reflects what opened it
  (`create_agent` for AGENT_STARTED, `invoke_workflow` for
  PHASE_STARTED). Filters keyed on operation name may need to be
  broadened.
- Failure events copy the terminal failure class onto the parent before
  ending it, so a single span carries the whole turn's outcome. Operational
  failures also set `error.type` and `Status(StatusCode.ERROR, …)`.
  Caller-driven cancellation remains typed as `cancelled` but is not marked as
  an OTel error.

If a downstream consumer was relying on every event being its own
root, switch to filtering by `gen_ai.agent.name` instead.

## Likely-relevant additions

- **`CheckpointSaver`** + `thread_id` + `idempotency_key` give you
  restart-resumable sessions. See [Durability](patterns/durability.md).
- **`RateLimiter` / `AsyncRateLimiter`** + `TokenBucket` for token-aware
  throttling. See [Rate limiting](patterns/rate-limiting.md).
- **`ProviderRouter`** strategies for cross-provider failover. See
  [Routing](patterns/routing.md).
- **`arun_parallel_tools`** runs sibling tool coroutines under
  `asyncio.TaskGroup` with proper structured-concurrency cancellation.

## Things that did NOT change

- The constructor of `AgentSession` (formerly `Orchestrator`).
- The shape of `OrchestrationSession` / `AsyncOrchestrationSession`.
- All existing test patterns (the rename is alias-compatible).
- The zero-runtime-dependency promise — every new feature is stdlib-only
  or behind an optional extra (`[otel]`).
