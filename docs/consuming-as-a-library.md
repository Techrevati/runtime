# Consuming the runtime as a library

`techrevati-runtime` is a zero-dependency, importable library. It does not own
your application's orchestration loop — you call its primitives from your own
agent-execution code. This page covers two situations: adding the runtime to a
fresh project, and migrating a project that already **vendored a fork** of these
primitives.

## Adding it to a project

The runtime has no required runtime dependencies, so it composes with any async
Python stack (Python 3.11+). Add it to your dependency set, pin a version, and
import primitives directly:

```python
from techrevati.runtime import (
    AgentSession, GovernancePlane, MaxBudgetLimit,
    PermissionEnforcer, PermissionPolicy, RolePermissionConfig,
    UsageTracker, resolve_pricing,
    RecoveryContext, attempt_recovery, backoff_delay, classify_exception,
)
```

Pin an exact version (`techrevati-runtime==X.Y.Z`); never float. The package
ships `py.typed`, so `mypy --strict` sees full types.

## Migrating from a vendored fork

If your project carries an in-tree copy of these primitives (a vendored
`agent_patterns`-style package that predates this library), do **not** copy the
runtime in alongside it — that creates a third copy of the same code to
maintain. Instead make the runtime the single source of truth and turn your
vendored package's shared modules into thin **re-export shims**. Your existing
import sites do not change.

### The re-export shim pattern

Replace the body of each vendored module that duplicates a runtime primitive
with a re-export:

```python
# your_vendored_pkg/recovery_recipes.py  (renamed to retry_policy in the runtime)
from techrevati.runtime import (  # noqa: F401
    RecoveryContext, RecoveryResult, RecoveryStep,
    attempt_recovery, backoff_delay, classify_exception, smaller_context_budget,
)
```

Call sites keep doing `from your_vendored_pkg.recovery_recipes import ...` and
resolve to the runtime symbols. Three kinds of gap need handling in the shim:

1. **Renamed modules.** The runtime exposes everything at the package top level
   (e.g. recovery/retry symbols), so a renamed module just re-exports from
   `techrevati.runtime`.
2. **Project-specific helpers.** Convenience functions and default config that
   were never library concerns (e.g. a `get_default_enforcer()` built from your
   own role map) stay local — define them in the shim using the runtime's public
   classes (`PermissionEnforcer`, `PermissionPolicy`, `RolePermissionConfig`).
3. **Private symbols.** If your code imported a private helper, switch to the
   public equivalent. For pricing, use the public `resolve_pricing(model)`
   instead of reaching into an internal resolver.

### Behavior differences to verify

Re-exporting names is necessary but not sufficient — the runtime is the
hardened, evolved version, so verify behavior, not just imports:

- Constructors validate more strictly; inputs a fork accepted loosely may now
  raise. Audit call sites that pass empty or out-of-range values.
- Guardrail violations are collected (a tuple of all violations at a stage),
  not first-only.
- Telemetry uses nested parent/child spans, not one-shot spans.
- Non-cost overruns raise `UsageLimitExceededError`; catch the
  `UsageBoundExceededError` base to cover both cost and non-cost bounds.

### Roll out incrementally

Shim one module at a time, run your test suite after each, and delete the
vendored real module once its shim is green. Keep only your project-specific
modules as real code. Each step is a one-file revert, so a bad step is isolated.
