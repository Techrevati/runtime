# techrevati-runtime

[![PyPI](https://img.shields.io/badge/pypi-techrevati--runtime-blue.svg)](https://pypi.org/project/techrevati-runtime/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Type Safe](https://img.shields.io/badge/py.typed-yes-brightgreen.svg)](https://peps.python.org/pep-0561/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](#design-goals)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Production-grade runtime primitives for multi-step LLM agent loops — sync **and** async, with retry classification, circuit-breaker protection, per-model cost tracking, opt-in budget enforcement, role-based tool gating, content guardrails, agent-to-agent handoffs, declarative policy, and OpenTelemetry GenAI semantic conventions out of the box. **Beta — 0.1.x; minor breaking changes possible until 0.2.0.**

```bash
pip install techrevati-runtime
# Or with OpenTelemetry:
pip install 'techrevati-runtime[otel]'
```

## Quick start

```python
from techrevati.runtime import (
    Orchestrator, UsageSnapshot, ModelPricing, register_pricing,
)

register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))

orch = Orchestrator(
    role="writer", phase="draft", project_id=1,
    budget_usd=10.0, enforce_budget=True, max_iterations=25,
)
with orch.session() as session:
    result, usage = session.run_turn(
        lambda: call_model(prompt),
        model="model-a",
        usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
        timeout=30.0,
    )
print(session.summary())
```

The session walks the worker through `INITIALIZING → RUNNING → COMPLETED`, classifies any exception that bubbles up into a typed failure scenario, attempts recovery once, enforces the budget, gates tool calls behind permissions and guardrails, and emits structured events to any sink you configure — without you wiring any of it by hand.

**Async sibling**: replace `with` with `async with`, `session()` with `asession()`, `run_turn` with `arun_turn`. Same parameters. `asyncio.CancelledError` cleanly transitions the worker to `CANCELLED`.

For an end-to-end example exercising every primitive (permissions + breaker + budget + guardrail + handoff + policy + OTel), see [`examples/tiny_agent.py`](examples/tiny_agent.py) and the [end-to-end tutorial](docs/tutorials/end-to-end.md).

## Design goals

- **Zero runtime dependencies.** Imports are stdlib only. OpenTelemetry is an optional `[otel]` extra.
- **Type-safe.** `py.typed` marker shipped; clean under `mypy --strict`.
- **Composable.** Every primitive (`CircuitBreaker`, `AsyncCircuitBreaker`, `RetryContext`, `QualityGate`, `PolicyEngine`, `UsageTracker`, `PermissionEnforcer`, `Guardrail`, `Handoff`) is usable standalone. The `Orchestrator` is just the wiring.
- **Thread-safe and async-safe.** `threading.Lock` in sync paths, `asyncio.Lock` in async paths. State is per-instance.
- **Configuration-free at the edges.** Pricing data is empty by default; phase thresholds are not hardcoded; permission roles are caller-defined. The runtime stays opinion-free about what your numbers mean.

## Primitives

| Module | Provides |
|---|---|
| `orchestrator` | `Orchestrator`, `OrchestrationSession`, `AsyncOrchestrationSession`, `AgentSession` |
| `circuit_breaker` | `CircuitBreaker`, `AsyncCircuitBreaker` (CLOSED/OPEN/HALF_OPEN with configurable probe permits) |
| `retry_policy` | `classify_exception`, `attempt_recovery` (sync + async), `backoff_delay` with full/equal/decorrelated jitter |
| `usage_tracking` | `UsageTracker`, `register_pricing`, `load_pricing_from_file`, `BudgetExceededError`, `has_pricing` |
| `agent_lifecycle` | `AgentRegistry`, `AgentWorker` with validated state machine including `CANCELLED` |
| `agent_events` | Typed lifecycle events + OpenTelemetry attribute bridge |
| `permissions` | Role × tool authorization, deny-first |
| `guardrails` | Pre-call + post-call content gating around `run_tool` / `arun_tool` |
| `handoffs` | `Handoff` value + `session.handoff_to()` agent-to-agent delegation |
| `policy_engine` | Composable conditions and rule evaluator with auto-elapsed time |
| `sinks` | `EventSink` / `UsageSink` Protocols + ring-buffered defaults |
| `otel` *(optional)* | `OpenTelemetrySink` + `OpenTelemetryUsageSink` emitting GenAI semconv spans/metrics |

## Showcase

### Async with handoff and guardrails

```python
import asyncio
from techrevati.runtime import (
    AllowAllGuardrail, AsyncCircuitBreaker, Orchestrator, UsageSnapshot,
)

cb = AsyncCircuitBreaker("model-api", failure_threshold=3, recovery_timeout_seconds=30.0)

async def main():
    orch = Orchestrator(
        role="writer", phase="draft",
        async_circuit_breaker=cb,
        guardrails=[AllowAllGuardrail()],
        max_iterations=10,
    )
    async with orch.asession() as session:
        text, _ = await session.arun_turn(
            lambda: acall_model(prompt),
            model="model-a",
            usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
            timeout=30.0,
        )
        handoff = session.handoff_to("editor", reason="review", context={"draft": text})
        print(f"handed off to {handoff.target_role}")

asyncio.run(main())
```

### OpenTelemetry observability

```python
from techrevati.runtime import Orchestrator
from techrevati.runtime.otel import OpenTelemetrySink, OpenTelemetryUsageSink

orch = Orchestrator(
    role="writer", phase="draft",
    event_sink=OpenTelemetrySink(agent_id="writer-001"),
    usage_sink=OpenTelemetryUsageSink(),
)
# Every AgentEvent now appears as an OTel span with gen_ai.operation.name,
# gen_ai.agent.id, gen_ai.usage.{input,output}_tokens. Drop-in compatible
# with any APM ingest that already understands GenAI semconv.
```

See [`docs/api/otel.md`](docs/api/otel.md) for the full attribute list and span name mapping.

### Standalone primitives

Pick just what you need. Each primitive is usable on its own without `Orchestrator`.

```python
from techrevati.runtime import (
    CircuitBreaker, CircuitOpenError,
    UsageTracker, UsageSnapshot,
    classify_exception, attempt_recovery, RecoveryContext,
)

cb = CircuitBreaker("downstream", failure_threshold=5, recovery_timeout_seconds=60.0)
result = cb.call(fetch, url, timeout=10)  # raises CircuitOpenError if tripped

ctx = RecoveryContext()
scenario = classify_exception(my_error)
recovery = attempt_recovery(scenario, ctx)  # returns RecoveryResult with steps to retry

tracker = UsageTracker()
tracker.record_turn("model-a", UsageSnapshot(input_tokens=5000, output_tokens=1200))
print(tracker.format_cost())
```

## Why not LangGraph / OpenAI Agents SDK?

`techrevati-runtime` is intentionally smaller and narrower than either:

- **LangGraph** is a *workflow engine* with durable execution, checkpointer protocols, and a graph model. Use it when your agent flow is a graph that needs to survive restarts and you're OK with the LangChain ecosystem footprint.
- **OpenAI Agents SDK** is a *cohesive runtime* tied to OpenAI's models, with default tracing through their dashboards. Use it when you're committed to OpenAI and want the smoothest path.
- **`techrevati-runtime`** is a *zero-dep primitive set*. Sync + async. Vendor-neutral. Emits OpenTelemetry GenAI semantic conventions so the same APM dashboards that consume OpenAI Agents SDK telemetry will pick us up too. Bring your own model client and your own persistence — the runtime stays opinion-free.

The runtime is **not** a durable workflow engine. Sessions are in-memory; a pluggable checkpointer is on the 0.2.0 roadmap. If you need restart-resumable workflows today, pair this with [Temporal](https://temporal.io/), [dbos](https://www.dbos.dev/), or LangGraph's checkpointer.

## Limitations (be honest with yourself before adopting)

- **Pricing must be registered.** The bundled `pricing.json` is intentionally empty. Without `register_pricing()` or `load_pricing_from_file()`, every cost calculation returns $0.00 (you will see a one-time warning per model).
- **Budget enforcement is opt-in.** Set `Orchestrator(enforce_budget=True)` to raise `BudgetExceededError`; the default merely records an event and continues.
- **Permissions are advisory.** `OrchestrationSession.run_tool()` enforces; `run_turn()` does not gate model calls. There is no sandbox — pair with OS-level isolation if needed.
- **No durable execution.** Sessions are in-memory and ephemeral. Pair with Temporal/dbos for restart-resumable workflows.
- **Default sinks are in-memory ring buffers.** Long-running sessions need a durable `EventSink` and `UsageSink` (e.g. `OpenTelemetrySink`, or your own).
- **`CircuitBreaker` state is per-process.** Each replica counts its own failures. Add a shared coordinator if you need fleet-wide breaker state.

## Status

`techrevati-runtime` is at version **0.1.0** (beta). This release ships async-first execution, the four standard primitives (Sessions, Tools, Handoffs, Guardrails), `max_iterations` cap, and OpenTelemetry GenAI semantic conventions. Minor breaking changes are possible between 0.1.x and 0.2.0 — they will be documented in [docs/migrating-from-0.0.x.md](docs/migrating-from-0.0.x.md) and gated by deprecation warnings. Pinning Python 3.11+ for `from __future__ import annotations` ergonomics and modern asyncio.

See [CHANGELOG.md](CHANGELOG.md) for the per-sprint release notes and [docs/tutorials/end-to-end.md](docs/tutorials/end-to-end.md) for a guided tour of every primitive.

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT — copyright © 2026 [TechRevati doo](https://techrevati.com). See [LICENSE](LICENSE).
