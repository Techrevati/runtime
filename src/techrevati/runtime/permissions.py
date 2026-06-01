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


def _normalize_name(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _normalize_tool_name(value: str) -> str:
    return _normalize_name(value, field_name="tool_name").lower()


def _normalize_mode(value: PermissionMode | int) -> PermissionMode:
    if isinstance(value, bool):
        raise TypeError("permission mode must be a PermissionMode")
    try:
        return PermissionMode(value)
    except TypeError as exc:
        raise TypeError("permission mode must be a PermissionMode") from exc
    except ValueError as exc:
        raise ValueError("permission mode must be a valid PermissionMode") from exc


def _validate_bool(field_name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


def _copy_role_config(config: RolePermissionConfig) -> RolePermissionConfig:
    if not isinstance(config, RolePermissionConfig):
        raise TypeError("role_configs values must be RolePermissionConfig instances")
    return RolePermissionConfig(
        role=config.role,
        mode=config.mode,
        allowed_tools=list(config.allowed_tools)
        if config.allowed_tools is not None
        else None,
        denied_tools=list(config.denied_tools),
    )


@dataclass(frozen=True)
class PermissionOutcome:
    """Result of a permission check."""

    allowed: bool
    reason: str | None = None
    active_mode: str = ""
    required_mode: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be a bool")
        if self.reason is not None and not isinstance(self.reason, str):
            raise TypeError("reason must be a string or None")
        if not isinstance(self.active_mode, str):
            raise TypeError("active_mode must be a string")
        if not isinstance(self.required_mode, str):
            raise TypeError("required_mode must be a string")

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

    def __post_init__(self) -> None:
        self.role = _normalize_name(self.role, field_name="role")
        self.mode = _normalize_mode(self.mode)
        if self.allowed_tools is not None:
            if not isinstance(self.allowed_tools, list):
                raise TypeError("allowed_tools must be a list or None")
            self.allowed_tools = [
                _normalize_name(tool, field_name="allowed_tools")
                for tool in self.allowed_tools
            ]
        if not isinstance(self.denied_tools, list):
            raise TypeError("denied_tools must be a list")
        self.denied_tools = [
            _normalize_name(tool, field_name="denied_tools")
            for tool in self.denied_tools
        ]


class PermissionPolicy:
    """Evaluates tool access based on role configs and tool requirements."""

    def __init__(
        self,
        role_configs: dict[str, RolePermissionConfig] | None = None,
        tool_requirements: dict[str, PermissionMode] | None = None,
        *,
        default_allow_unknown_roles: bool = True,
    ) -> None:
        self._default_allow_unknown_roles = _validate_bool(
            "default_allow_unknown_roles",
            default_allow_unknown_roles,
        )
        self._role_configs: dict[str, RolePermissionConfig] = {}
        for role, config in (role_configs or {}).items():
            normalized_role = _normalize_name(role, field_name="role")
            copied = _copy_role_config(config)
            if copied.role != normalized_role:
                raise ValueError("role_configs keys must match config.role")
            self._role_configs[normalized_role] = copied
        self._tool_requirements = {
            _normalize_tool_name(tool): _normalize_mode(mode)
            for tool, mode in (tool_requirements or {}).items()
        }

    def authorize(self, role: str, tool_name: str) -> PermissionOutcome:
        role = _normalize_name(role, field_name="role")
        tool_name = _normalize_name(tool_name, field_name="tool_name")
        config = self._role_configs.get(role)
        if config is None:
            if not self._default_allow_unknown_roles:
                return PermissionOutcome(
                    allowed=False,
                    reason="no config for role, default deny",
                    active_mode="N/A",
                    required_mode="N/A",
                )
            return PermissionOutcome(
                allowed=True,
                reason="no config for role, default allow",
            )

        # 1. Deny list first (highest precedence)
        tool_lower = _normalize_tool_name(tool_name)
        for denied in config.denied_tools:
            if tool_lower == denied.lower():
                return PermissionOutcome(
                    allowed=False,
                    reason=f"tool '{tool_name}' is explicitly denied for role '{role}'",
                    active_mode=config.mode.name,
                    required_mode="N/A",
                )

        # 2. Mode check
        required = self._tool_requirements.get(tool_lower, PermissionMode.READ_ONLY)
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
