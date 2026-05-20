# Provider routing

`ProviderRouter` is a strategy interface for picking which provider to
call when the runtime decides to switch (e.g. after
`RecoveryStep.SWITCH_PROVIDER` fires). Three reference implementations
ship: first-acceptable (static), rotating (round-robin), and
weighted by config.

## Quick example

```python
from techrevati.runtime import (
    Orchestrator, StaticProviderRouter,
)

router = StaticProviderRouter(("model-a", "model-b", "model-c"))
orch = Orchestrator(
    role="writer", phase="draft",
    provider_router=router,
)
```

The runtime exposes the router through `session.provider_router` so
caller code that owns the actual call can ask for a next provider on a
failure recovery branch:

```python
next_provider = session.provider_router.select(
    scenario=FailureScenario.PROVIDER_FAILURE,
    attempt=2,
    current="model-a",
    exclude=("model-already-429d",),
)
```

`select` is allowed to return `None` — that's the signal to escalate
to the recipe's `EscalationPolicy` rather than continue retrying.

## When to use

- You have a stable fallback list and want a one-line drop-in for
  "pick the next one".
- You're balancing across providers for cost reasons and want strict
  rotation (`RoundRobinProviderRouter`).
- You want a weighted preference that survives config reloads
  (`WeightedProviderRouter`) — e.g. "prefer cheap-A, fall back to
  premium-B only at peak hours".

## When NOT to use

- Single-provider workloads. Routing only makes sense when there is
  somewhere to fall back to.
- Mid-call provider switching — these routers fire between calls,
  not inside one. Streaming partial output across providers requires
  a custom strategy.

## Comparison

| Router | State | Strategy | Determinism |
|---|---|---|---|
| `StaticProviderRouter` | stateless | first non-excluded in order | yes |
| `RoundRobinProviderRouter` | cursor | strict rotation | yes, given input order |
| `WeightedProviderRouter` | stateless | highest weight, ties to earliest declaration | yes |

## Anti-patterns

- **Putting the same provider name in the list twice** — round-robin
  treats them as distinct slots and the weighted router will pick the
  first one. If you want preference, use weights, not duplicates.
- **Selecting before classifying the failure.** Call `select` only
  after `classify_exception` actually returns a scenario that calls
  for a switch (`LLM_ERROR` with `SWITCH_PROVIDER` in the recipe, or
  `PROVIDER_FAILURE`).
- **Mutating the `providers` tuple at runtime.** All three
  implementations capture the tuple at construction; rebuild the
  router when the list changes.

## See also

- [Retry policy](retry.md) — where `SWITCH_PROVIDER` originates.
- [Rate limiting](rate-limiting.md) — pair with router via the
  `exclude` argument to skip providers that 429'd this minute.
