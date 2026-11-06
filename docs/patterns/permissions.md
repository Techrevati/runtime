# Permissions

Deny-first role × tool authorization. `denied_tools` is checked first, then `mode` (a totally ordered `READ_ONLY < WORKSPACE_WRITE < FULL_ACCESS`), then an optional `allowed_tools` whitelist.

This module ships configuration-free — define your own roles and tool requirements.

## Usage

```python
from techrevati.runtime import (
    PermissionMode, RolePermissionConfig, PermissionPolicy, PermissionEnforcer,
)

policy = PermissionPolicy(
    role_configs={
        "writer": RolePermissionConfig(
            role="writer",
            mode=PermissionMode.FULL_ACCESS,
            denied_tools=["dangerous_tool"],
        ),
        "reader": RolePermissionConfig(role="reader", mode=PermissionMode.READ_ONLY),
        "reviewer": RolePermissionConfig(
            role="reviewer",
            mode=PermissionMode.WORKSPACE_WRITE,
            allowed_tools=["review_code", "comment"],  # whitelist mode
        ),
    },
    tool_requirements={
        "dangerous_tool": PermissionMode.FULL_ACCESS,
        "publish":        PermissionMode.FULL_ACCESS,
    },
)
enforcer = PermissionEnforcer(policy)

outcome = enforcer.check("writer", "any_tool")
if not outcome.allowed:
    raise RuntimeError(outcome.reason)
```

## Resolution order

1. **Deny list.** Any tool in `denied_tools` is rejected, regardless of mode.
2. **Mode comparison.** If the role's mode is lower than the tool's required mode, reject.
3. **Allowed list.** If `allowed_tools` is set, the tool must be in it.
4. **Default-allow.** If none of the above triggers a denial, allow.

A role is identified by its **exact** name. A name that matches a configured role
only after folding case, Unicode compatibility form (NFKC), or invisible characters,
such as `ADMIN` when `admin` is configured, is **denied** whatever
`default_allow_unknown_roles` says. Treating it as `admin` would grant access its
caller was not given, and treating it as unknown could escape `admin`'s deny list.

Tool names match regardless of those spellings, so `DELETE` and `delete` followed by
a zero-width space or variation selector are both `delete`. Homoglyphs from other
scripts, such as a Cyrillic `е`, stay distinct, so prefer `allowed_tools` over deny
lists when tool names come from untrusted input. `tool_requirements` keys that
collide after folding, tool names that are empty after folding, and two role configs
whose names differ only by surrounding whitespace are rejected at construction.

A role with no config at all is **denied** by default, so a typo in a role name cannot
silently grant every tool. Pass `default_allow_unknown_roles=True` only where a role is
deliberately allowed everything it is not explicitly denied.

## API

```python
class PermissionMode(IntEnum): READ_ONLY=1, WORKSPACE_WRITE=2, FULL_ACCESS=3

@dataclass
class RolePermissionConfig:
    role: str
    mode: PermissionMode
    allowed_tools: list[str] | None = None
    denied_tools: list[str] = []

class PermissionPolicy:
    def __init__(self, role_configs=None, tool_requirements=None): ...
    def authorize(role: str, tool_name: str) -> PermissionOutcome

class PermissionEnforcer:
    def __init__(self, policy: PermissionPolicy): ...
    def check(role: str, tool_name: str) -> PermissionOutcome
    def is_allowed(role: str, tool_name: str) -> bool
    def filter_tools(role: str, tools: list[Callable]) -> list[Callable]
```

`filter_tools` reads `tool.__name__` from each callable.

## Status

`permissions` is intentionally thin: role × tool only. No argument-level restrictions, no quotas, no time-of-day rules. Compose with your own layer if you need those.
