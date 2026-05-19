"""
Permissions — Role-based tool access gating.

Deny-first authorization: denied_tools is checked first, then mode
comparison, then the allowed_tools whitelist. Each role has a
PermissionMode that determines which tools it can use.

This module is configuration-free by design; callers supply their
own RolePermissionConfig and tool_requirements.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class PermissionMode(IntEnum):
    """Ordered permission levels. Higher grants more access."""

    READ_ONLY = 1
    WORKSPACE_WRITE = 2
    FULL_ACCESS = 3


@dataclass(frozen=True)
class PermissionOutcome:
    """Result of a permission check."""

    allowed: bool
    reason: str | None = None
    active_mode: str = ""
    required_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"allowed": self.allowed}
        if self.reason:
            d["reason"] = self.reason
        if self.active_mode:
            d["active_mode"] = self.active_mode
        if self.required_mode:
            d["required_mode"] = self.required_mode
        return d


@dataclass
class RolePermissionConfig:
    """Permission configuration for a single role."""

    role: str
    mode: PermissionMode
    allowed_tools: list[str] | None = None  # None = all at mode level
    denied_tools: list[str] = field(default_factory=list)


class PermissionPolicy:
    """Evaluates tool access based on role configs and tool requirements."""

    def __init__(
        self,
        role_configs: dict[str, RolePermissionConfig] | None = None,
        tool_requirements: dict[str, PermissionMode] | None = None,
    ) -> None:
        self._role_configs = role_configs or {}
        self._tool_requirements = tool_requirements or {}

    def authorize(self, role: str, tool_name: str) -> PermissionOutcome:
        config = self._role_configs.get(role)
        if config is None:
            return PermissionOutcome(
                allowed=True,
                reason="no config for role, default allow",
            )

        # 1. Deny list first (highest precedence)
        tool_lower = tool_name.lower()
        for denied in config.denied_tools:
            if tool_lower == denied.lower():
                return PermissionOutcome(
                    allowed=False,
                    reason=f"tool '{tool_name}' is explicitly denied for role '{role}'",
                    active_mode=config.mode.name,
                    required_mode="N/A",
                )

        # 2. Mode check
        required = self._tool_requirements.get(tool_name, PermissionMode.READ_ONLY)
        if config.mode < required:
            return PermissionOutcome(
                allowed=False,
                reason=(
                    f"role '{role}' has {config.mode.name} "
                    f"but tool requires {required.name}"
                ),
                active_mode=config.mode.name,
                required_mode=required.name,
            )

        # 3. Allowed list (if specified, tool must be in it)
        if config.allowed_tools is not None:
            allowed_lower = [t.lower() for t in config.allowed_tools]
            if tool_lower not in allowed_lower:
                return PermissionOutcome(
                    allowed=False,
                    reason=f"tool '{tool_name}' not in allowed list for role '{role}'",
                    active_mode=config.mode.name,
                    required_mode=required.name,
                )

        return PermissionOutcome(
            allowed=True,
            active_mode=config.mode.name,
            required_mode=required.name,
        )


class PermissionEnforcer:
    """Wraps PermissionPolicy for convenient enforcement."""

    def __init__(self, policy: PermissionPolicy) -> None:
        self._policy = policy

    def check(self, role: str, tool_name: str) -> PermissionOutcome:
        return self._policy.authorize(role, tool_name)

    def is_allowed(self, role: str, tool_name: str) -> bool:
        return self._policy.authorize(role, tool_name).allowed

    def filter_tools(
        self, role: str, tools: list[Callable[..., Any]]
    ) -> list[Callable[..., Any]]:
        """Return only tools the role is permitted to use."""
        return [
            t
            for t in tools
            if self._policy.authorize(role, getattr(t, "__name__", str(t))).allowed
        ]
