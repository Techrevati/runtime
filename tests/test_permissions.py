"""Tests for techrevati.runtime.permissions"""

from techrevati.runtime.permissions import (
    PermissionMode, RolePermissionConfig, PermissionPolicy,
    PermissionEnforcer,
)


def test_mode_ordering():
    assert PermissionMode.READ_ONLY < PermissionMode.WORKSPACE_WRITE
    assert PermissionMode.WORKSPACE_WRITE < PermissionMode.FULL_ACCESS


def test_read_only_cannot_use_write_tools():
    policy = PermissionPolicy(
        role_configs={"reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)},
        tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
    )
    result = policy.authorize("reader", "expand_features")
    assert result.allowed is False
    assert "READ_ONLY" in result.reason


def test_full_access_can_use_all():
    policy = PermissionPolicy(
        role_configs={"writer": RolePermissionConfig("writer", PermissionMode.FULL_ACCESS)},
        tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
    )
    assert policy.authorize("writer", "expand_features").allowed is True


def test_denied_tools_override_mode():
    config = RolePermissionConfig(
        "writer", PermissionMode.FULL_ACCESS,
        denied_tools=["dangerous_tool"],
    )
    policy = PermissionPolicy(role_configs={"writer": config})
    assert policy.authorize("writer", "dangerous_tool").allowed is False
    assert policy.authorize("writer", "safe_tool").allowed is True


def test_allowed_list_restricts():
    config = RolePermissionConfig(
        "reviewer", PermissionMode.WORKSPACE_WRITE,
        allowed_tools=["review_code", "assess_readiness"],
    )
    policy = PermissionPolicy(role_configs={"reviewer": config})
    assert policy.authorize("reviewer", "review_code").allowed is True
    assert policy.authorize("reviewer", "expand_features").allowed is False


def test_unknown_role_defaults_to_allow():
    policy = PermissionPolicy(role_configs={})
    assert policy.authorize("UNKNOWN", "any_tool").allowed is True


def test_filter_tools():
    def tool_a(): pass
    def tool_b(): pass
    tool_a.__name__ = "review_code"
    tool_b.__name__ = "expand_features"

    enforcer = PermissionEnforcer(PermissionPolicy(
        role_configs={"reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)},
        tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
    ))
    filtered = enforcer.filter_tools("reader", [tool_a, tool_b])
    names = [t.__name__ for t in filtered]
    assert "review_code" in names
    assert "expand_features" not in names


def test_enforcer_is_allowed_convenience():
    enforcer = PermissionEnforcer(PermissionPolicy(
        role_configs={"writer": RolePermissionConfig("writer", PermissionMode.FULL_ACCESS)},
    ))
    assert enforcer.is_allowed("writer", "any_tool") is True
