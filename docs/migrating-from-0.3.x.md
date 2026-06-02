# Migrating from 0.3.x to 0.4.0

0.4.0 is the EU AI Act compliance line. It is **additive** for almost everyone —
the compliance kit, MCP integration, typed outputs, session memory, and
step-level durability are all new, opt-in surface. There is one breaking change.

## Breaking change: the `Orchestrator` alias is removed

`Orchestrator` was a deprecated compatibility alias for `AgentSession` (it has
emitted a `DeprecationWarning` since 0.2.1, noting removal "no earlier than
0.4.0"). It is now gone.

```python
# Before (0.3.x)
from techrevati.runtime import Orchestrator
agent = Orchestrator(role="writer", phase="draft")

# After (0.4.0)
from techrevati.runtime import AgentSession
agent = AgentSession(role="writer", phase="draft")
```

The constructor and behavior are identical — this is a name change only. If you
consume the runtime through a re-export shim, update the shim's export list.

## New in 0.4.0 (all opt-in, no migration required)

- **EU AI Act compliance kit** — `from techrevati.runtime.compliance import
  EUAIActComplianceKit`; wire via `AgentSession(compliance=kit)` or attach just
  the audit log via `AgentSession(audit_log=...)`. See [EU AI Act](eu-ai-act/index.md).
- **MCP tools** — `pip install 'techrevati-runtime[mcp]'`; `MCPToolAdapter`. See
  [MCP Tools](patterns/mcp.md).
- **Typed outputs** — `OutputSpec[T]` (`JsonOutputSpec`, `RegexOutputSpec`,
  `CallableOutputSpec`). See [Typed Outputs](patterns/typed-outputs.md).
- **Session memory** — `ConversationMemory` + compaction. See
  [Session Memory](patterns/memory.md).
- **Step-level durability** — `StepCheckpointSaver.put_step` /
  `get_step` / `list_steps`. See [Durability](patterns/durability.md).
- **PostgreSQL / Redis** — `[postgres]` / `[redis]` extras + adapter recipes.

## Behavioral notes carried from 0.2.x / 0.3.x

If you are jumping several versions, these earlier changes still apply:

- `GuardrailViolatedError.violations` is a tuple of *all* guardrails that fired at
  a stage (was first-only before 0.2.1).
- Non-cost overruns raise `UsageLimitExceededError`, not `BudgetExceededError`.
  Catch the common base `UsageBoundExceededError` for both.
- The OpenTelemetry sink emits nested parent/child spans (since 0.2.0), not
  one-shot spans — APM filters keyed on `gen_ai.operation.name` may need
  broadening.
- 0.3.x constructors validate inputs more strictly (`__post_init__`); inputs that
  were silently accepted before (empty `role`, non-int `project_id`, negative
  token counts) now raise.

## Not changed in 0.4.0

`ProviderRouter` and `RecoveryRecipe.step_retries` defaults are unchanged in
0.4.0. (Earlier roadmaps floated reworking them here; doing so without a concrete
motivation would be a gratuitous break for downstream consumers, so it is
deferred.)
