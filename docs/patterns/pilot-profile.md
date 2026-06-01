# Pilot Profile

`build_pilot_profile` creates an explicit component set for controlled release
candidate pilots. It does not change `AgentSession` defaults and it is not
exported from the package root; import it from the `pilot` submodule.

```python
from techrevati.runtime import AgentSession, PermissionMode
from techrevati.runtime.pilot import build_pilot_profile

profile = build_pilot_profile(
    role="writer",
    allowed_tools=("lookup_order", "summarize_case"),
    budget_usd=3.00,
    permission_mode=PermissionMode.WORKSPACE_WRITE,
)

agent = AgentSession(
    role="writer",
    phase="pilot",
    **profile.agent_session_kwargs(),
)
```

The profile enables:

- allowlist tool permissions for the configured role,
- fail-closed permission checks for unknown roles,
- prompt-injection checks on tool outputs,
- hard-stop governance limits for turns, cost, consecutive failures, and tool
  calls.

The base `PermissionPolicy` keeps its compatibility default for unknown roles
unless callers opt into `default_allow_unknown_roles=False`. The pilot helper
sets that option for you, so only the configured role can reach the allowed
tools.

Use `tool_deny_patterns=` for temporary pre-call tool-name blocks during a
pilot. Keep the allowed-tool list small and review every tool body separately;
the runtime authorizes tool names, but it does not sandbox tool code.

Each call to `profile.agent_session_kwargs()` returns a fresh governance plane
so session counters do not leak across pilot runs.
