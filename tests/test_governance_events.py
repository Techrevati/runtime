"""governance.breach / governance.alert events surface in EventSink."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    AgentEventName,
    AgentSession,
    GovernanceBreachError,
    GovernancePlane,
    MaxBudgetLimit,
    MaxIterationsLimit,
    ModelPricing,
    RingBufferEventSink,
    UsageSnapshot,
    register_pricing,
)

_MODEL = "governance-events-test-model"


@pytest.fixture(scope="module", autouse=True)
def _pricing():
    register_pricing(
        _MODEL,
        ModelPricing(input_per_million=1_000_000.0, output_per_million=1_000_000.0),
        on_conflict="overwrite",
    )


def test_governance_breach_emits_event_before_raising():
    sink = RingBufferEventSink()
    plane = GovernancePlane(
        limits=(MaxIterationsLimit(value=1, on_breach="terminate"),)
    )
    sess = AgentSession(
        role="r", phase="p", governance=plane, event_sink=sink, max_iterations=100
    )

    with pytest.raises(GovernanceBreachError):
        with sess.session() as session:
            session.run_turn(
                lambda: "ok",
                model=_MODEL,
                usage=UsageSnapshot(input_tokens=10, output_tokens=10),
            )
            session.run_turn(
                lambda: "ok",
                model=_MODEL,
                usage=UsageSnapshot(input_tokens=10, output_tokens=10),
            )

    breaches = [e for e in sink.events if e.event == AgentEventName.GOVERNANCE_BREACH]
    assert len(breaches) == 1
    breach = breaches[0]
    assert breach.data["limit_name"] == "max_iterations"
    assert breach.data["observed"] == 2.0
    assert breach.data["ceiling"] == 1.0
    assert breach.data["scope"] == "session"


def test_governance_alert_emits_event_without_raising():
    sink = RingBufferEventSink()
    plane = GovernancePlane(limits=(MaxBudgetLimit(value=0.00001, on_breach="alert"),))
    sess = AgentSession(
        role="r", phase="p", governance=plane, event_sink=sink, max_iterations=10
    )

    with sess.session() as session:
        # One $0.01 turn — exceeds the $1e-5 alert cap, but doesn't raise.
        session.run_turn(
            lambda: "ok",
            model=_MODEL,
            usage=UsageSnapshot(input_tokens=10, output_tokens=10),
        )

    alerts = [e for e in sink.events if e.event == AgentEventName.GOVERNANCE_ALERT]
    assert len(alerts) >= 1
    alert = alerts[0]
    assert alert.data["limit_name"] == "max_budget_usd"
    assert alert.data["scope"] == "session"


def test_no_governance_means_no_governance_events():
    sink = RingBufferEventSink()
    sess = AgentSession(role="r", phase="p", event_sink=sink, max_iterations=3)
    with sess.session() as session:
        session.run_turn(
            lambda: "ok",
            model=_MODEL,
            usage=UsageSnapshot(input_tokens=10, output_tokens=10),
        )

    governance_events = [
        e
        for e in sink.events
        if e.event
        in (AgentEventName.GOVERNANCE_BREACH, AgentEventName.GOVERNANCE_ALERT)
    ]
    assert governance_events == []
