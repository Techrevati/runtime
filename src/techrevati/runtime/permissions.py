"""
Permissions — Role-based tool access gating.

Deny-first authorization: denied_tools is checked first, then mode
comparison, then the allowed_tools whitelist. Each role has a
PermissionMode that determines which tools it can use.

This module is configuration-free by design; callers supply their
own RolePermissionConfig and tool_requirements.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from techrevati.runtime._internal import (
    _validate_bool,
)


class PermissionMode(IntEnum):
    """Ordered permission levels. Higher grants more access."""

    READ_ONLY = 1
    WORKSPACE_WRITE = 2
    FULL_ACCESS = 3


# Unicode Default_Ignorable_Code_Point outside category C, plus blank lookalikes that
# render as nothing. NFKC runs first and maps the Hangul fillers U+3164 and U+FFA0
# to U+1160.
_IGNORABLE_RANGES = (
    (0x034F, 0x034F),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x2800, 0x2800),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFFA0, 0xFFA0),
    (0xE0100, 0xE01EF),
)


def _normalize_name(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _is_ignorable(ch: str) -> bool:
    """Invisible characters that must not change which name a string refers to."""
    code = ord(ch)
    return unicodedata.category(ch)[0] == "C" or any(
        low <= code <= high for low, high in _IGNORABLE_RANGES
    )


def _match_key(value: str, *, field_name: str) -> str:
    """Comparison key for a name, ignoring how it was spelled.

    Case, Unicode compatibility forms, and invisible characters collapse, so
    ``DELETE``, ``ｄｅｌｅｔｅ`` and ``delete`` followed by a zero-width space share a
    key. Homoglyphs from other scripts (Cyrillic ``е``) stay distinct.
    """
    name = _normalize_name(value, field_name=field_name)
    composed = unicodedata.normalize("NFKC", name)
    visible = "".join(ch for ch in composed if not _is_ignorable(ch))
    # Compose again: removing an invisible character can leave a letter next to a
    # combining mark ("cafe" + U+200B + U+0301 is "café"), and folding can decompose.
    key = unicodedata.normalize("NFKC", visible.casefold()).strip()
    if not key:
        raise ValueError(f"{field_name} must be a non-empty string")
    return key


def _normalize_tool_name(value: str) -> str:
    return _match_key(value, field_name="tool_name")


def _normalize_mode(value: PermissionMode | int) -> PermissionMode:
    if isinstance(value, bool):
        raise TypeError("permission mode must be a PermissionMode")
    try:
        return PermissionMode(value)
    except TypeError as exc:
        raise TypeError("permission mode must be a PermissionMode") from exc
    except ValueError as exc:
        raise ValueError("permission mode must be a valid PermissionMode") from exc


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
            for tool in self.allowed_tools:
                _match_key(tool, field_name="allowed_tools")
        if not isinstance(self.denied_tools, list):
            raise TypeError("denied_tools must be a list")
        self.denied_tools = [
            _normalize_name(tool, field_name="denied_tools")
            for tool in self.denied_tools
        ]
        for tool in self.denied_tools:
            _match_key(tool, field_name="denied_tools")


@dataclass(frozen=True)
class _CompiledRole:
    """A role config with its tool lists reduced to comparison keys."""

    config: RolePermissionConfig
    denied: frozenset[str]
    allowed: frozenset[str] | None


def _compile_role(config: RolePermissionConfig) -> _CompiledRole:
    allowed = config.allowed_tools
    return _CompiledRole(
        config=config,
        denied=frozenset(_normalize_tool_name(t) for t in config.denied_tools),
        allowed=(
            None
            if allowed is None
            else frozenset(_normalize_tool_name(t) for t in allowed)
        ),
    )


class PermissionPolicy:
    """Evaluates tool access based on role configs and tool requirements.

    A role is identified by its **exact** name. A name that only matches a
    configured role after folding case, Unicode form, or invisible characters is
    denied, whatever ``default_allow_unknown_roles`` says: treating it as that role
    could grant access its caller was not given, and treating it as unknown could
    escape that role's deny list.

    A role with no config at all is denied unless ``default_allow_unknown_roles`` is
    set, so a typo in a role name cannot silently grant every tool.

    Tool names match regardless of those spellings, so a deny list cannot be escaped
    by respelling. Keys that collide after folding are rejected at construction.
    """

    def __init__(
        self,
        role_configs: dict[str, RolePermissionConfig] | None = None,
        tool_requirements: dict[str, PermissionMode] | None = None,
        *,
        default_allow_unknown_roles: bool = False,
    ) -> None:
        self._default_allow_unknown_roles = _validate_bool(
            "default_allow_unknown_roles",
            default_allow_unknown_roles,
        )
        self._roles: dict[str, _CompiledRole] = {}
        self._role_keys: dict[str, str] = {}
        for role, config in (role_configs or {}).items():
            normalized_role = _normalize_name(role, field_name="role")
            copied = _copy_role_config(config)
            if copied.role != normalized_role:
                raise ValueError("role_configs keys must match config.role")
            if normalized_role in self._roles:
                raise ValueError(f"duplicate role config for {normalized_role!r}")
            self._roles[normalized_role] = _compile_role(copied)
            self._role_keys[_match_key(normalized_role, field_name="role")] = (
                normalized_role
            )
        self._tool_requirements: dict[str, PermissionMode] = {}
        for tool, mode in (tool_requirements or {}).items():
            key = _normalize_tool_name(tool)
            if key in self._tool_requirements:
                raise ValueError(f"tool_requirements keys collide for {tool!r}")
            self._tool_requirements[key] = _normalize_mode(mode)

    def authorize(self, role: str, tool_name: str) -> PermissionOutcome:
        role = _normalize_name(role, field_name="role")
        tool_name = _normalize_name(tool_name, field_name="tool_name")
        compiled = self._roles.get(role)
        if compiled is None:
            configured = self._role_keys.get(_match_key(role, field_name="role"))
            if configured is not None:
                return PermissionOutcome(
                    allowed=False,
                    reason=(
                        f"role {role!r} is not an exact match for configured role "
                        f"{configured!r}; denied rather than treated as either"
                    ),
                    active_mode="N/A",
                    required_mode="N/A",
                )
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
        config = compiled.config

        # 1. Deny list first (highest precedence)
        tool_lower = _normalize_tool_name(tool_name)
        if tool_lower in compiled.denied:
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
        if compiled.allowed is not None and tool_lower not in compiled.allowed:
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
