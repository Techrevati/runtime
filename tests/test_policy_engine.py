"""Tests for techrevati.runtime.policy_engine"""

from typing import Any, cast

import pytest

from techrevati.runtime.policy_engine import (
    AgentFailed,
    AllAgentsComplete,
    And,
    CostExceeded,
    GateBelow,
    Or,
    PhaseCompleted,
    PhaseContext,
    PolicyAction,
    PolicyActionData,
    PolicyCondition,
    PolicyEngine,
    PolicyRule,
    QualityAt,
    RetryExhausted,
    TimedOut,
)
from techrevati.runtime.quality_gate import QualityLevel


def _ctx(**kwargs) -> PhaseContext:
    defaults = {
        "phase": "draft",
        "quality_level": QualityLevel.STANDARD,
        "gate_score": 85.0,
        "gate_threshold": 82.0,
        "completed_roles": {"writer", "reviewer"},
        "failed_roles": set(),
        "all_roles": {"writer", "reviewer"},
        "elapsed_seconds": 100,
        "phase_completed": True,
    }
    defaults.update(kwargs)
    return PhaseContext(**defaults)


def _advance_on_quality_rule() -> PolicyRule:
    return PolicyRule(
        name="advance-on-quality",
        condition=And([PhaseCompleted(), QualityAt(QualityLevel.STANDARD)]),
        actions=[
            PolicyActionData(PolicyAction.GENERATE_HANDOFF),
            PolicyActionData(PolicyAction.ADVANCE_PHASE),
        ],
        priority=10,
    )


def _recover_on_failure_rule() -> PolicyRule:
    return PolicyRule(
        name="recover-on-failure",
        condition=AgentFailed(),
        actions=[PolicyActionData(PolicyAction.RECOVER_ONCE)],
        priority=15,
    )


def _feedback_below_threshold_rule() -> PolicyRule:
    return PolicyRule(
        name="store-feedback-on-below",
        condition=And([PhaseCompleted(), GateBelow(82.0)]),
        actions=[PolicyActionData(PolicyAction.STORE_GATE_FEEDBACK)],
        priority=20,
    )


def _escalate_on_exhausted_rule() -> PolicyRule:
    return PolicyRule(
        name="escalate-on-exhausted",
        condition=RetryExhausted(),
        actions=[PolicyActionData(PolicyAction.ESCALATE)],
        priority=25,
    )


def _abort_on_timeout_rule(seconds: float = 3600) -> PolicyRule:
    return PolicyRule(
        name="abort-on-timeout",
        condition=TimedOut(seconds),
        actions=[PolicyActionData(PolicyAction.ABORT_PHASE)],
        priority=5,
    )


def _abort_on_budget_rule(budget: float) -> PolicyRule:
    return PolicyRule(
        name="abort-on-budget",
        condition=CostExceeded(budget),
        actions=[PolicyActionData(PolicyAction.ABORT_PHASE)],
        priority=7,
    )


def test_advance_fires_on_quality():
    engine = PolicyEngine([_advance_on_quality_rule()])
    actions = engine.evaluate(_ctx())
    names = [a.action for a in actions]
    assert PolicyAction.GENERATE_HANDOFF in names
    assert PolicyAction.ADVANCE_PHASE in names


def test_feedback_fires_on_gate_failure():
    engine = PolicyEngine([_feedback_below_threshold_rule()])
    ctx = _ctx(gate_score=75.0, quality_level=QualityLevel.MINIMAL)
    actions = engine.evaluate(ctx)
    assert PolicyAction.STORE_GATE_FEEDBACK in [a.action for a in actions]


def test_recover_fires_on_agent_failure():
    engine = PolicyEngine([_recover_on_failure_rule()])
    ctx = _ctx(failed_roles={"writer"}, phase_completed=False)
    actions = engine.evaluate(ctx)
    assert PolicyAction.RECOVER_ONCE in [a.action for a in actions]


def test_escalate_on_retry_exhausted():
    engine = PolicyEngine([_escalate_on_exhausted_rule()])
    ctx = _ctx(retry_exhausted_scenarios={"llm_timeout"}, phase_completed=False)
    actions = engine.evaluate(ctx)
    assert PolicyAction.ESCALATE in [a.action for a in actions]


def test_abort_on_timeout():
    engine = PolicyEngine([_abort_on_timeout_rule()])
    ctx = _ctx(elapsed_seconds=7200, phase_completed=False)
    actions = engine.evaluate(ctx)
    assert PolicyAction.ABORT_PHASE in [a.action for a in actions]


def test_abort_on_budget():
    engine = PolicyEngine([_abort_on_budget_rule(5.0)])
    ctx = _ctx(total_cost_usd=10.0, phase_completed=False)
    actions = engine.evaluate(ctx)
    assert PolicyAction.ABORT_PHASE in [a.action for a in actions]


def test_rules_sorted_by_priority():
    rules = [
        PolicyRule(
            "low",
            PhaseCompleted(),
            [PolicyActionData(PolicyAction.NOTIFY)],
            priority=99,
        ),
        PolicyRule(
            "high",
            PhaseCompleted(),
            [PolicyActionData(PolicyAction.ADVANCE_PHASE)],
            priority=1,
        ),
    ]
    engine = PolicyEngine(rules)
    assert engine.rules[0].name == "high"
    assert engine.rules[1].name == "low"


def test_and_combinator():
    cond = And([PhaseCompleted(), QualityAt(QualityLevel.STANDARD)])
    assert cond.matches(_ctx()) is True
    assert cond.matches(_ctx(quality_level=QualityLevel.MINIMAL)) is False


def test_or_combinator():
    cond = Or([AgentFailed("writer"), TimedOut(3600)])
    assert cond.matches(_ctx(failed_roles=set(), elapsed_seconds=100)) is False
    assert cond.matches(_ctx(failed_roles={"writer"})) is True


def test_empty_and_is_true():
    assert And([]).matches(_ctx()) is True


def test_empty_or_is_false():
    assert Or([]).matches(_ctx()) is False


def test_no_match_returns_empty():
    rules = [
        PolicyRule(
            "never",
            AgentFailed("NONEXISTENT"),
            [PolicyActionData(PolicyAction.ESCALATE)],
        ),
    ]
    engine = PolicyEngine(rules)
    assert engine.evaluate(_ctx()) == []


def test_cost_exceeded_condition():
    cond = CostExceeded(5.0)
    assert cond.matches(_ctx(total_cost_usd=10.0)) is True
    assert cond.matches(_ctx(total_cost_usd=3.0)) is False


@pytest.mark.parametrize(
    "factory",
    [
        lambda: GateBelow(-1.0),
        lambda: GateBelow(float("nan")),
        lambda: TimedOut(-1.0),
        lambda: TimedOut(float("inf")),
        lambda: CostExceeded(-1.0),
        lambda: CostExceeded(float("nan")),
    ],
)
def test_numeric_conditions_reject_invalid_thresholds(factory):
    with pytest.raises(ValueError):
        factory()


def test_targeted_conditions_reject_blank_names():
    with pytest.raises(ValueError, match="role"):
        AgentFailed("")
    with pytest.raises(ValueError, match="scenario"):
        RetryExhausted("   ")


def test_condition_reprs_are_stable():
    conditions = [
        And([PhaseCompleted()]),
        Or([AgentFailed("writer")]),
        QualityAt(QualityLevel.STANDARD),
        PhaseCompleted(),
        AgentFailed("writer"),
        GateBelow(82.0),
        RetryExhausted("llm_timeout"),
        TimedOut(3600),
        AllAgentsComplete(),
        CostExceeded(5.0),
    ]

    for condition in conditions:
        assert type(condition).__name__ in repr(condition)


def test_base_condition_requires_matches_override():
    with pytest.raises(TypeError, match="abstract class"):
        PolicyCondition()


def test_retry_exhausted_targeted_scenario():
    cond = RetryExhausted("llm_timeout")
    assert cond.matches(_ctx(retry_exhausted_scenarios={"llm_timeout"})) is True
    assert cond.matches(_ctx(retry_exhausted_scenarios={"rate_limit"})) is False


def test_all_agents_complete():
    cond = AllAgentsComplete()
    ctx = _ctx(
        completed_roles={"writer", "reviewer"},
        failed_roles=set(),
        all_roles={"writer", "reviewer"},
    )
    assert cond.matches(ctx) is True
    ctx2 = _ctx(
        completed_roles={"writer"}, failed_roles=set(), all_roles={"writer", "reviewer"}
    )
    assert cond.matches(ctx2) is False


def test_all_agents_complete_requires_configured_roles():
    assert AllAgentsComplete().matches(PhaseContext()) is False


def test_gate_below():
    assert GateBelow(82.0).matches(_ctx(gate_score=75.0)) is True
    assert GateBelow(82.0).matches(_ctx(gate_score=90.0)) is False


def test_action_data_to_dict():
    ad = PolicyActionData(PolicyAction.ESCALATE, {"reason": "test"})
    d = ad.to_dict()
    assert d["action"] == "escalate"
    assert d["params"]["reason"] == "test"


def test_action_data_to_dict_returns_params_copy():
    params: dict[str, Any] = {"reason": "initial", "nested": {"values": [1]}}
    ad = PolicyActionData(PolicyAction.NOTIFY, params)
    params["nested"]["values"].append(2)

    assert ad.params == {"reason": "initial", "nested": {"values": [1]}}

    payload = ad.to_dict()

    payload["params"]["reason"] = "mutated"
    payload["params"]["nested"]["values"].append(3)

    assert ad.params == {"reason": "initial", "nested": {"values": [1]}}


def test_action_data_rejects_invalid_params():
    with pytest.raises(TypeError, match="params"):
        PolicyActionData(PolicyAction.NOTIFY, cast(Any, []))
    with pytest.raises(TypeError, match="params keys"):
        PolicyActionData(PolicyAction.NOTIFY, cast(Any, {1: "bad"}))


def test_action_data_accepts_action_values_and_omits_empty_params():
    ad = PolicyActionData("notify")  # type: ignore[arg-type]

    assert ad.action == PolicyAction.NOTIFY
    assert ad.to_dict() == {"action": "notify"}


def test_action_data_rejects_invalid_action():
    with pytest.raises(ValueError, match="action"):
        PolicyActionData("not-real")  # type: ignore[arg-type]


def test_policy_rule_rejects_invalid_configuration():
    with pytest.raises(ValueError, match="name"):
        PolicyRule("", PhaseCompleted(), [PolicyActionData(PolicyAction.NOTIFY)])
    with pytest.raises(ValueError, match="actions"):
        PolicyRule("no-actions", PhaseCompleted(), [])
    with pytest.raises(TypeError, match="actions"):
        PolicyRule("bad-actions", PhaseCompleted(), cast(Any, object()))
    with pytest.raises(TypeError, match="PolicyActionData"):
        PolicyRule(
            "bad-action-item",
            PhaseCompleted(),
            cast(Any, [object()]),
        )
    with pytest.raises(TypeError, match="priority"):
        PolicyRule(
            "bad-priority",
            PhaseCompleted(),
            [PolicyActionData(PolicyAction.NOTIFY)],
            priority=cast(Any, True),
        )
    with pytest.raises(ValueError, match="matches"):
        PolicyRule(
            "bad-condition",
            object(),  # type: ignore[arg-type]
            [PolicyActionData(PolicyAction.NOTIFY)],
        )


def test_policy_rule_trims_name_and_copies_actions():
    action = PolicyActionData(PolicyAction.NOTIFY, {"nested": {"values": [1]}})
    actions = [action]

    rule = PolicyRule("  notify  ", PhaseCompleted(), actions)
    actions.append(PolicyActionData(PolicyAction.ESCALATE))
    assert isinstance(rule.actions, tuple)

    assert rule.name == "notify"
    assert [action.action for action in rule.actions] == [PolicyAction.NOTIFY]

    action.params["nested"]["values"].append(2)  # type: ignore[index]
    assert rule.actions[0].params == {"nested": {"values": [1]}}


def test_phase_context_rejects_invalid_numbers():
    with pytest.raises(ValueError, match="elapsed_seconds"):
        PhaseContext(elapsed_seconds=-1.0)
    with pytest.raises(ValueError, match="total_cost_usd"):
        PhaseContext(total_cost_usd=float("nan"))
    with pytest.raises(ValueError, match="gate_score"):
        PhaseContext(gate_score=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="quality level"):
        PhaseContext(quality_level=999)  # type: ignore[arg-type]


def test_phase_context_rejects_invalid_shape_and_copies_sets():
    completed = {"writer"}
    ctx = PhaseContext(phase=" draft ", completed_roles=completed)
    completed.add("mutated")

    assert ctx.phase == "draft"
    assert ctx.completed_roles == {"writer"}

    with pytest.raises(TypeError, match="phase"):
        PhaseContext(phase=cast(Any, 123))
    with pytest.raises(ValueError, match="phase"):
        PhaseContext(phase=" ")
    with pytest.raises(TypeError, match="completed_roles"):
        PhaseContext(completed_roles=cast(Any, ["writer"]))
    with pytest.raises(ValueError, match="failed_roles"):
        PhaseContext(failed_roles={""})
    with pytest.raises(TypeError, match="phase_completed"):
        PhaseContext(phase_completed=cast(Any, "yes"))


def test_policy_engine_copies_rules_and_returned_actions():
    rule = PolicyRule(
        "notify",
        PhaseCompleted(),
        [PolicyActionData(PolicyAction.NOTIFY, {"nested": {"values": [1]}})],
        priority=1,
    )
    engine = PolicyEngine([rule])

    rule.actions[0].params["nested"]["values"].append(2)  # type: ignore[index]
    first_actions = engine.evaluate(_ctx())
    assert first_actions[0].params == {"nested": {"values": [1]}}

    first_actions[0].params["nested"]["values"].append(3)  # type: ignore[index]
    assert engine.evaluate(_ctx())[0].params == {"nested": {"values": [1]}}

    exposed_rules = engine.rules
    exposed_rules[0].actions[0].params["nested"]["values"].append(4)  # type: ignore[index]
    assert engine.evaluate(_ctx())[0].params == {"nested": {"values": [1]}}


def test_policy_engine_rejects_invalid_rules_container():
    with pytest.raises(TypeError, match="rules"):
        PolicyEngine(cast(Any, object()))
    with pytest.raises(TypeError, match="PolicyRule"):
        PolicyEngine(cast(Any, [object()]))


def test_multiple_rules_fire_in_priority_order():
    rules = [
        _abort_on_timeout_rule(seconds=50),  # priority 5
        _advance_on_quality_rule(),  # priority 10
    ]
    engine = PolicyEngine(rules)
    actions = engine.evaluate(_ctx(elapsed_seconds=100))
    action_names = [a.action for a in actions]
    # Abort fires first (priority 5) then advance (priority 10)
    assert action_names[0] == PolicyAction.ABORT_PHASE
