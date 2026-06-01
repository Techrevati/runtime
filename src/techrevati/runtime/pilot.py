"""Pilot-ready runtime profile helpers.

The helpers in this module do not change ``AgentSession`` defaults. They build
an explicit profile that applications can opt into for controlled release
candidate pilots:

- deny-by-default tool permissions through an allowed-tool list,
- prompt-injection checks on tool outputs,
- hard-stop governance limits for turns, cost, failures, and tool calls.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from techrevati.runtime.governance import (
    GovernancePlane,
    MaxBudgetLimit,
    MaxConsecutiveFailuresLimit,
    MaxIterationsLimit,
    MaxToolCallsLimit,
)
from techrevati.runtime.guardrails import (
    AsyncGuardrail,
    Guardrail,
    GuardrailStage,
    PatternGuardrail,
    PromptInjectionGuardrail,
)
from techrevati.runtime.permissions import (
    PermissionEnforcer,
    PermissionMode,
    PermissionPolicy,
    RolePermissionConfig,
)


def _validate_role(role: str) -> str:
    if not isinstance(role, str):
        raise TypeError("role must be a string")
    if not role.strip():
        raise ValueError("role must not be empty")
    return role.strip()


def _validate_name_sequence(
    field_name: str,
    values: Sequence[str],
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must contain only strings")
        value = value.strip()
        if not value:
            raise ValueError(f"{field_name} must not contain empty values")
        key = value.lower()
        if key in seen:
            raise ValueError(f"{field_name} must not contain duplicate values")
        seen.add(key)
        normalized.append(value)
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(normalized)


def _validate_pattern_sequence(
    field_name: str,
    values: Sequence[str],
) -> tuple[str, ...]:
    """Validate a sequence of regular-expression patterns.

    Unlike :func:`_validate_name_sequence`, patterns are NOT ``.strip()``-ed
    (leading/trailing whitespace can be significant in a regex) and are
    deduplicated case-sensitively (``Secret`` and ``secret`` are distinct
    signatures). Each pattern must compile, so a malformed regex fails here
    with a clear message instead of deep inside guardrail construction.
    """
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must contain only strings")
        if not value:
            raise ValueError(f"{field_name} must not contain empty patterns")
        if value in seen:
            raise ValueError(f"{field_name} must not contain duplicate values")
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(
                f"{field_name} contains an invalid regular expression: {value!r}"
            ) from exc
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _validate_positive_int(field_name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _validate_positive_float(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    amount = float(value)
    if not math.isfinite(amount):
        raise ValueError(f"{field_name} must be finite")
    if amount <= 0:
        raise ValueError(f"{field_name} must be positive")
    return amount


def _validate_permission_mode(value: PermissionMode | int) -> PermissionMode:
    if isinstance(value, bool):
        raise TypeError("permission_mode must be a PermissionMode")
    try:
        return PermissionMode(value)
    except TypeError as exc:
        raise TypeError("permission_mode must be a PermissionMode") from exc
    except ValueError as exc:
        raise ValueError("permission_mode must be a valid PermissionMode") from exc


@dataclass(frozen=True)
class PilotRuntimeProfile:
    """Reusable pilot components for ``AgentSession`` construction.

    ``GovernancePlane`` contains mutable counters, so ``agent_session_kwargs()``
    returns a fresh plane every time. Use the returned mapping directly when
    constructing an ``AgentSession``.
    """

    permissions: PermissionEnforcer
    guardrails: tuple[Guardrail | AsyncGuardrail, ...]
    governance: GovernancePlane
    max_iterations: int

    def agent_session_kwargs(self) -> dict[str, Any]:
        """Return kwargs suitable for ``AgentSession(...)``."""
        return {
            "permissions": self.permissions,
            "guardrails": list(self.guardrails),
            "governance": GovernancePlane(limits=self.governance.limits),
            "max_iterations": self.max_iterations,
        }


def build_pilot_profile(
    *,
    role: str,
    allowed_tools: Sequence[str],
    budget_usd: float,
    permission_mode: PermissionMode | int = PermissionMode.READ_ONLY,
    denied_tools: Sequence[str] = (),
    tool_requirements: Mapping[str, PermissionMode | int] | None = None,
    max_iterations: int = 25,
    max_tool_calls: int = 100,
    max_consecutive_failures: int = 3,
    prompt_injection_stages: tuple[GuardrailStage, ...] = ("post",),
    extra_prompt_injection_patterns: Sequence[str] = (),
    tool_deny_patterns: Sequence[str] = (),
) -> PilotRuntimeProfile:
    """Build a conservative profile for a controlled RC pilot.

    The profile is intentionally explicit: callers must supply the role,
    allowed tools, and session budget. Unknown tools are denied by the allowed
    list; governance breaches terminate the session; prompt-injection signatures
    are checked on tool outputs by default.
    """
    role = _validate_role(role)
    allowed = _validate_name_sequence("allowed_tools", allowed_tools)
    denied = _validate_name_sequence("denied_tools", denied_tools, allow_empty=True)
    permission_mode = _validate_permission_mode(permission_mode)
    max_iterations = _validate_positive_int("max_iterations", max_iterations)
    max_tool_calls = _validate_positive_int("max_tool_calls", max_tool_calls)
    max_consecutive_failures = _validate_positive_int(
        "max_consecutive_failures", max_consecutive_failures
    )
    budget_usd = _validate_positive_float("budget_usd", budget_usd)
    extra_patterns = _validate_pattern_sequence(
        "extra_prompt_injection_patterns",
        extra_prompt_injection_patterns,
    )
    deny_patterns = _validate_pattern_sequence(
        "tool_deny_patterns",
        tool_deny_patterns,
    )

    requirements = dict.fromkeys(allowed, permission_mode)
    for tool, mode in (tool_requirements or {}).items():
        requirements[tool] = _validate_permission_mode(mode)

    permissions = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                role: RolePermissionConfig(
                    role=role,
                    mode=permission_mode,
                    allowed_tools=list(allowed),
                    denied_tools=list(denied),
                )
            },
            tool_requirements=requirements,
            default_allow_unknown_roles=False,
        )
    )

    guardrails: list[Guardrail | AsyncGuardrail] = [
        PromptInjectionGuardrail(
            stages=prompt_injection_stages,
            extra_patterns=extra_patterns,
        )
    ]
    if deny_patterns:
        guardrails.append(
            PatternGuardrail(
                list(deny_patterns),
                stages=("pre",),
                name="pilot_tool_deny",
            )
        )

    governance = GovernancePlane(
        limits=(
            MaxIterationsLimit(value=max_iterations, on_breach="terminate"),
            MaxBudgetLimit(value=budget_usd, on_breach="terminate"),
            MaxConsecutiveFailuresLimit(
                value=max_consecutive_failures,
                on_breach="terminate",
            ),
            MaxToolCallsLimit(value=max_tool_calls, on_breach="terminate"),
        )
    )

    return PilotRuntimeProfile(
        permissions=permissions,
        guardrails=tuple(guardrails),
        governance=governance,
        max_iterations=max_iterations,
    )


__all__ = ["PilotRuntimeProfile", "build_pilot_profile"]
