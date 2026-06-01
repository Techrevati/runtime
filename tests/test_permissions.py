"""Tests for techrevati.runtime.permissions"""

from typing import Any, cast

import pytest

from techrevati.runtime.permissions import (
    PermissionEnforcer,
    PermissionMode,
    PermissionOutcome,
    PermissionPolicy,
    RolePermissionConfig,
)


def test_mode_ordering():
    assert PermissionMode.READ_ONLY < PermissionMode.WORKSPACE_WRITE
    assert PermissionMode.WORKSPACE_WRITE < PermissionMode.FULL_ACCESS


def test_read_only_cannot_use_write_tools():
    policy = PermissionPolicy(
        role_configs={
            "reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)
        },
        tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
    )
    result = policy.authorize("reader", "expand_features")
    assert result.allowed is False
    assert "READ_ONLY" in result.reason


def test_tool_requirements_are_case_insensitive():
    policy = PermissionPolicy(
        role_configs={
            "reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)
        },
        tool_requirements={"Expand_Features": PermissionMode.FULL_ACCESS},
    )

    result = policy.authorize("reader", "expand_features")

    assert result.allowed is False
    assert result.required_mode == "FULL_ACCESS"


def test_full_access_can_use_all():
    policy = PermissionPolicy(
        role_configs={
            "writer": RolePermissionConfig("writer", PermissionMode.FULL_ACCESS)
        },
        tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
    )
    assert policy.authorize("writer", "expand_features").allowed is True


def test_denied_tools_override_mode():
    config = RolePermissionConfig(
        "writer",
        PermissionMode.FULL_ACCESS,
        denied_tools=["dangerous_tool"],
    )
    policy = PermissionPolicy(role_configs={"writer": config})
    assert policy.authorize("writer", "dangerous_tool").allowed is False
    assert policy.authorize("writer", "safe_tool").allowed is True


def test_denied_tools_trim_and_match_case_insensitively():
    config = RolePermissionConfig(
        "writer",
        PermissionMode.FULL_ACCESS,
        denied_tools=[" Dangerous_Tool "],
    )
    policy = PermissionPolicy(role_configs={"writer": config})

    assert policy.authorize("writer", "dangerous_tool").allowed is False


def test_allowed_list_restricts():
    config = RolePermissionConfig(
        "reviewer",
        PermissionMode.WORKSPACE_WRITE,
        allowed_tools=["review_code", "assess_readiness"],
    )
    policy = PermissionPolicy(role_configs={"reviewer": config})
    assert policy.authorize("reviewer", "review_code").allowed is True
    assert policy.authorize("reviewer", "expand_features").allowed is False


def test_permission_config_rejects_blank_names():
    for value in ("", "   "):
        try:
            RolePermissionConfig(value, PermissionMode.READ_ONLY)
        except ValueError as exc:
            assert "role" in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError("blank role was accepted")

        try:
            RolePermissionConfig(
                "reader",
                PermissionMode.READ_ONLY,
                allowed_tools=[value],
            )
        except ValueError as exc:
            assert "allowed_tools" in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError("blank allowed tool was accepted")


def test_permission_config_rejects_invalid_shapes():
    with pytest.raises(TypeError, match="role"):
        RolePermissionConfig(cast(Any, 123), PermissionMode.READ_ONLY)
    with pytest.raises(TypeError, match="permission mode"):
        RolePermissionConfig("reader", cast(Any, True))
    with pytest.raises(ValueError, match="valid PermissionMode"):
        RolePermissionConfig("reader", cast(Any, 999))
    with pytest.raises(TypeError, match="allowed_tools"):
        RolePermissionConfig(
            "reader",
            PermissionMode.READ_ONLY,
            allowed_tools=cast(Any, ("tool",)),
        )
    with pytest.raises(TypeError, match="denied_tools"):
        RolePermissionConfig(
            "reader",
            PermissionMode.READ_ONLY,
            denied_tools=cast(Any, ("tool",)),
        )


def test_policy_rejects_invalid_authorize_inputs():
    policy = PermissionPolicy()

    for role, tool in [("", "tool"), ("role", "")]:
        try:
            policy.authorize(role, tool)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion clarity
            raise AssertionError("blank authorize input was accepted")


def test_policy_rejects_invalid_tool_requirement_key():
    try:
        PermissionPolicy(tool_requirements={"": PermissionMode.FULL_ACCESS})
    except ValueError as exc:
        assert "tool_name" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("blank tool requirement was accepted")


def test_policy_rejects_invalid_configuration_shapes():
    with pytest.raises(TypeError, match="RolePermissionConfig"):
        PermissionPolicy(role_configs={"reader": cast(Any, object())})
    with pytest.raises(ValueError, match="match config.role"):
        PermissionPolicy(
            role_configs={
                "reader": RolePermissionConfig("writer", PermissionMode.READ_ONLY)
            }
        )
    with pytest.raises(TypeError, match="permission mode"):
        PermissionPolicy(tool_requirements={"write": cast(Any, True)})
    with pytest.raises(TypeError, match="default_allow_unknown_roles"):
        PermissionPolicy(default_allow_unknown_roles=cast(Any, "yes"))


def test_policy_copies_role_configs_at_construction():
    config = RolePermissionConfig(
        "writer",
        PermissionMode.FULL_ACCESS,
        allowed_tools=["safe_tool"],
        denied_tools=["dangerous_tool"],
    )
    policy = PermissionPolicy(role_configs={"writer": config})

    config.mode = PermissionMode.READ_ONLY
    assert config.allowed_tools is not None
    config.allowed_tools.append("new_tool")
    config.denied_tools.clear()

    assert policy.authorize("writer", "safe_tool").allowed is True
    assert policy.authorize("writer", "dangerous_tool").allowed is False
    assert policy.authorize("writer", "new_tool").allowed is False


def test_unknown_role_defaults_to_allow():
    policy = PermissionPolicy(role_configs={})
    assert policy.authorize("UNKNOWN", "any_tool").allowed is True


def test_unknown_role_can_fail_closed():
    policy = PermissionPolicy(
        role_configs={
            "writer": RolePermissionConfig("writer", PermissionMode.READ_ONLY)
        },
        default_allow_unknown_roles=False,
    )

    outcome = policy.authorize("reviewer", "read_doc")

    assert outcome.allowed is False
    assert outcome.reason == "no config for role, default deny"
    assert outcome.active_mode == "N/A"
    assert outcome.required_mode == "N/A"


def test_filter_tools():
    def tool_a():
        pass

    def tool_b():
        pass

    tool_a.__name__ = "review_code"
    tool_b.__name__ = "expand_features"

    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "reader": RolePermissionConfig("reader", PermissionMode.READ_ONLY)
            },
            tool_requirements={"expand_features": PermissionMode.FULL_ACCESS},
        )
    )
    filtered = enforcer.filter_tools("reader", [tool_a, tool_b])
    names = [t.__name__ for t in filtered]
    assert "review_code" in names
    assert "expand_features" not in names


def test_enforcer_is_allowed_convenience():
    enforcer = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "writer": RolePermissionConfig("writer", PermissionMode.FULL_ACCESS)
            },
        )
    )
    assert enforcer.is_allowed("writer", "any_tool") is True


def test_permission_outcome_to_dict_strips_empty_fields():
    """to_dict() must omit reason / active_mode / required_mode when blank."""
    minimal = PermissionOutcome(allowed=True).to_dict()
    assert minimal == {"allowed": True}


def test_permission_outcome_to_dict_includes_populated_fields():
    """All populated fields must appear in the serialized dict."""
    full = PermissionOutcome(
        allowed=False,
        reason="explicitly denied",
        active_mode="READ_ONLY",
        required_mode="FULL_ACCESS",
    ).to_dict()
    assert full == {
        "allowed": False,
        "reason": "explicitly denied",
        "active_mode": "READ_ONLY",
        "required_mode": "FULL_ACCESS",
    }


def test_permission_outcome_rejects_invalid_shape():
    with pytest.raises(TypeError, match="allowed"):
        PermissionOutcome(allowed=cast(Any, 1))
    with pytest.raises(TypeError, match="reason"):
        PermissionOutcome(allowed=False, reason=cast(Any, object()))
    with pytest.raises(TypeError, match="active_mode"):
        PermissionOutcome(allowed=False, active_mode=cast(Any, object()))
    with pytest.raises(TypeError, match="required_mode"):
        PermissionOutcome(allowed=False, required_mode=cast(Any, object()))


def test_authorize_returns_dict_serializable_outcome():
    """End-to-end: a denial outcome's to_dict() must round-trip useful data."""
    policy = PermissionPolicy(
        role_configs={"r": RolePermissionConfig("r", PermissionMode.READ_ONLY)},
        tool_requirements={"write": PermissionMode.FULL_ACCESS},
    )
    outcome = policy.authorize("r", "write").to_dict()
    assert outcome["allowed"] is False
    assert outcome["active_mode"] == "READ_ONLY"
    assert outcome["required_mode"] == "FULL_ACCESS"
    assert "READ_ONLY" in outcome["reason"]
