"""GovernancePlane — hard-stop limit evaluation + enforcement."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    GovernanceBreachError,
    GovernancePlane,
    GovernanceState,
    LimitOutcome,
    MaxBudgetLimit,
    MaxConsecutiveFailuresLimit,
    MaxIterationsLimit,
    MaxToolCallsLimit,
)


def test_no_limits_means_no_breach():
    plane = GovernancePlane(limits=())
    assert plane.evaluate() == []
    assert plane.enforce() == []


def test_below_ceiling_does_not_breach():
    plane = GovernancePlane(limits=(MaxIterationsLimit(value=5),))
    plane.state.record_turn_start()
    plane.state.record_turn_start()
    outcomes = plane.evaluate()
    assert len(outcomes) == 1
    assert outcomes[0].breached is False
    assert outcomes[0].observed == 2.0
    assert outcomes[0].ceiling == 5.0
    plane.enforce()  # must not raise


def test_strict_greater_than_threshold_for_breach():
    """observed == value is NOT a breach; only strictly greater is."""
    plane = GovernancePlane(limits=(MaxIterationsLimit(value=3),))
    for _ in range(3):
        plane.state.record_turn_start()
    # observed == 3, ceiling == 3 → not breached
    outcomes = plane.evaluate()
    assert outcomes[0].breached is False
    plane.state.record_turn_start()  # observed == 4 now
    outcomes = plane.evaluate()
    assert outcomes[0].breached is True


def test_terminate_breach_raises_governance_breach_error():
    plane = GovernancePlane(
        limits=(MaxIterationsLimit(value=2, on_breach="terminate"),),
    )
    for _ in range(3):
        plane.state.record_turn_start()
    with pytest.raises(GovernanceBreachError) as exc_info:
        plane.enforce()
    err = exc_info.value
    assert err.limit_name == "max_iterations"
    assert err.observed == 3.0
    assert err.ceiling == 2.0
    assert err.scope == "session"


def test_alert_breach_does_not_raise():
    plane = GovernancePlane(
        limits=(MaxBudgetLimit(value=1.0, on_breach="alert"),),
    )
    plane.state.record_cost(5.0)
    outcomes = plane.enforce()  # must not raise
    breached = [o for o in outcomes if o.breached]
    assert len(breached) == 1
    assert breached[0].on_breach == "alert"
    assert breached[0].observed == 5.0


def test_first_terminate_breach_raises_even_if_later_limit_would_also_breach():
    """Enforcement stops on the first terminate-breach in declaration order."""
    plane = GovernancePlane(
        limits=(
            MaxIterationsLimit(value=2, on_breach="terminate"),
            MaxBudgetLimit(value=1.0, on_breach="terminate"),
        ),
    )
    for _ in range(5):
        plane.state.record_turn_start()
    plane.state.record_cost(10.0)
    with pytest.raises(GovernanceBreachError) as exc_info:
        plane.enforce()
    # First declared limit breaches first
    assert exc_info.value.limit_name == "max_iterations"


def test_alerts_dont_short_circuit_terminate():
    """An alert breach before a terminate breach should not block enforcement."""
    plane = GovernancePlane(
        limits=(
            MaxBudgetLimit(value=1.0, on_breach="alert"),  # this breaches first
            MaxIterationsLimit(value=2, on_breach="terminate"),  # but this still raises
        ),
    )
    plane.state.record_cost(10.0)
    for _ in range(5):
        plane.state.record_turn_start()
    with pytest.raises(GovernanceBreachError) as exc_info:
        plane.enforce()
    assert exc_info.value.limit_name == "max_iterations"


def test_consecutive_failures_resets_on_success():
    plane = GovernancePlane(
        limits=(MaxConsecutiveFailuresLimit(value=3, on_breach="terminate"),),
    )
    plane.state.record_failure()
    plane.state.record_failure()
    plane.state.record_failure()  # observed == 3, not breached
    plane.enforce()
    plane.state.record_success()  # resets to 0
    plane.state.record_failure()
    outcomes = plane.evaluate()
    assert outcomes[0].observed == 1.0
    assert outcomes[0].breached is False


def test_tool_calls_limit_breach_carries_metric():
    plane = GovernancePlane(
        limits=(MaxToolCallsLimit(value=10, on_breach="terminate"),),
    )
    for _ in range(15):
        plane.state.record_tool_call()
    with pytest.raises(GovernanceBreachError) as exc_info:
        plane.enforce()
    assert exc_info.value.limit_name == "max_tool_calls"
    assert exc_info.value.observed == 15.0
    assert exc_info.value.ceiling == 10.0


def test_governance_state_starts_at_zero():
    state = GovernanceState()
    assert state.turns == 0
    assert state.tool_calls == 0
    assert state.consecutive_failures == 0
    assert state.cost_usd == 0.0


@pytest.mark.parametrize(
    ("kwargs", "field_name"),
    [
        ({"turns": -1}, "turns"),
        ({"tool_calls": -1}, "tool_calls"),
        ({"consecutive_failures": -1}, "consecutive_failures"),
        ({"cost_usd": -0.01}, "cost_usd"),
        ({"cost_usd": float("nan")}, "cost_usd"),
    ],
)
def test_governance_state_rejects_invalid_initial_values(kwargs, field_name):
    with pytest.raises(ValueError, match=field_name):
        GovernanceState(**kwargs)


@pytest.mark.parametrize("cost", [-0.01, float("nan"), float("inf")])
def test_governance_state_rejects_invalid_cost_delta(cost):
    state = GovernanceState()

    with pytest.raises(ValueError, match="cost_usd"):
        state.record_cost(cost)

    assert state.cost_usd == 0.0


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MaxIterationsLimit(value=-1),
        lambda: MaxIterationsLimit(value=float("nan")),
        lambda: MaxBudgetLimit(value=float("inf")),
        lambda: MaxToolCallsLimit(value=True),  # type: ignore[arg-type]
    ],
)
def test_governance_limits_reject_invalid_values(factory):
    with pytest.raises(ValueError, match="value"):
        factory()


def test_governance_limits_reject_invalid_scope_and_breach_action():
    with pytest.raises(ValueError, match="scope"):
        MaxIterationsLimit(value=1, scope="tenant")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not supported"):
        MaxIterationsLimit(value=1, scope="thread")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not supported"):
        MaxIterationsLimit(value=1, scope="project")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="on_breach"):
        MaxIterationsLimit(value=1, on_breach="page")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="name"):
        MaxIterationsLimit(value=1, name=" ")


def test_governance_plane_normalizes_limit_sequence_and_rejects_invalid_items():
    limits = [MaxIterationsLimit(value=1)]
    plane = GovernancePlane(limits=limits)  # type: ignore[arg-type]
    limits.append(MaxBudgetLimit(value=1))

    assert plane.limits == (MaxIterationsLimit(value=1),)

    with pytest.raises(ValueError, match="governance limit"):
        GovernancePlane(limits=(object(),))  # type: ignore[arg-type]


def test_governance_plane_requires_governance_state():
    with pytest.raises(ValueError, match="GovernanceState"):
        GovernancePlane(
            limits=(MaxIterationsLimit(value=1),),
            state=object(),  # type: ignore[arg-type]
        )


def test_limit_outcome_is_serializable_dataclass():
    """Outcomes must be inspectable for audit logging."""
    plane = GovernancePlane(limits=(MaxIterationsLimit(value=5),))
    plane.state.record_turn_start()
    outcomes = plane.evaluate()
    assert isinstance(outcomes[0], LimitOutcome)
    assert outcomes[0].limit_name == "max_iterations"


def test_limit_outcome_rejects_invalid_shape():
    with pytest.raises(TypeError, match="breached"):
        LimitOutcome(
            breached=1,  # type: ignore[arg-type]
            limit_name="limit",
            observed=2,
            ceiling=1,
            scope="session",
            on_breach="terminate",
        )
    with pytest.raises(ValueError, match="limit_name"):
        LimitOutcome(
            breached=True,
            limit_name=" ",
            observed=2,
            ceiling=1,
            scope="session",
            on_breach="terminate",
        )
    with pytest.raises(ValueError, match="must match"):
        LimitOutcome(
            breached=True,
            limit_name="limit",
            observed=1,
            ceiling=2,
            scope="session",
            on_breach="terminate",
        )


def test_governance_breach_error_validates_constructor_inputs():
    with pytest.raises(ValueError, match="limit_name"):
        GovernanceBreachError(
            limit_name="",
            observed=2,
            ceiling=1,
            scope="session",
        )
    with pytest.raises(ValueError, match="observed"):
        GovernanceBreachError(
            limit_name="limit",
            observed=float("nan"),
            ceiling=1,
            scope="session",
        )
    with pytest.raises(ValueError, match="exceed"):
        GovernanceBreachError(
            limit_name="limit",
            observed=1,
            ceiling=1,
            scope="session",
        )
