# techrevati-runtime

Production runtime primitives for multi-step LLM agent loops.

```bash
pip install techrevati-runtime
```

## What's in the box

- **Orchestrator** — one session wires lifecycle, retry classification, circuit breaker, permissions, cost tracking, and policy
- **Retry policy** — typed failure scenarios with recipe-driven recovery steps
- **Circuit breaker** — three-state (CLOSED / OPEN / HALF_OPEN) protection
- **Usage tracking** — per-model cost with caller-provided pricing
- **Quality gate** — graduated pass/fail evaluation
- **Agent lifecycle** — validated state machine with audit log
- **Agent events** — typed lifecycle events with OpenTelemetry attribute bridge
- **Permissions** — deny-first role × tool gating
- **Policy engine** — composable rules over a phase context

## Design tenets

- Zero runtime dependencies — stdlib only
- Type-safe (PEP 561 `py.typed`, clean under `mypy --strict`)
- Thread-safe primitives
- Configuration-free at the edges — caller supplies pricing, thresholds, roles

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
    circuit_breaker=CircuitBreaker("model-api", failure_threshold=5),
)

with orch.session() as session:
    result, usage = session.run_turn(
        lambda: call_model(prompt),
        model="model-a",
        usage=UsageSnapshot(input_tokens=5000, output_tokens=1200),
    )

print(session.summary())
```

## Next steps

- [Getting Started](getting-started.md) — installation and full walk-through
- [Primitives](patterns/orchestrator.md) — module-by-module reference
- [Changelog](changelog.md)

## License

MIT — copyright © 2026 [TechRevati doo](https://techrevati.com).
