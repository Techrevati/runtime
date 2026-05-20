"""GovernancePlane integration with AgentSession — sync + async."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    AgentSession,
    GovernanceBreachError,
    GovernancePlane,
    MaxBudgetLimit,
    MaxConsecutiveFailuresLimit,
    MaxIterationsLimit,
    MaxToolCallsLimit,
    ModelPricing,
    UsageSnapshot,
    register_pricing,
)

_MODEL = "governance-integration-test-model"


@pytest.fixture(scope="module", autouse=True)
def _ensure_model_pricing():
    register_pricing(
        _MODEL,
        ModelPricing(input_per_million=1_000_000.0, output_per_million=1_000_000.0),
        on_conflict="overwrite",
    )


def _ten_token_usage() -> UsageSnapshot:
    return UsageSnapshot(input_tokens=10, output_tokens=10)


def test_no_governance_means_no_change_in_behavior():
    """Default path (governance=None) must be identical to 0.2.0."""
    sess = AgentSession(role="r", phase="p", max_iterations=3)
    with sess.session() as session:
        for _ in range(3):
            result, _ = session.run_turn(
                lambda: "ok", model=_MODEL, usage=_ten_token_usage()
            )
            assert result == "ok"


def test_max_iterations_governance_raises_terminal_error():
    plane = GovernancePlane(
        limits=(MaxIterationsLimit(value=2, on_breach="terminate"),)
    )
    sess = AgentSession(role="r", phase="p", governance=plane, max_iterations=100)
    with pytest.raises(GovernanceBreachError) as exc_info:
        with sess.session() as session:
            for _ in range(3):
                session.run_turn(lambda: "ok", model=_MODEL, usage=_ten_token_usage())
    assert exc_info.value.limit_name == "max_iterations"
    assert exc_info.value.observed == 3.0


def test_governance_breach_skips_recovery_path():
    """A GovernanceBreachError must NOT trigger recovery_attempted events."""
    plane = GovernancePlane(
        limits=(MaxIterationsLimit(value=1, on_breach="terminate"),)
    )
    sess = AgentSession(role="r", phase="p", governance=plane, max_iterations=100)

    captured_events = []

    def _spy_emit(event):
        captured_events.append(event)

    try:
        with sess.session() as session:
            # Hook the event stream to capture
            original = session._emit_event
            session._emit_event = lambda e: (_spy_emit(e), original(e))[1]  # type: ignore
            session.run_turn(lambda: "ok", model=_MODEL, usage=_ten_token_usage())
            session.run_turn(lambda: "ok", model=_MODEL, usage=_ten_token_usage())
    except GovernanceBreachError:
        pass

    recovery_events = [
        e for e in captured_events if "recovery" in e.event.value.lower()
    ]
    assert recovery_events == [], (
        f"recovery was attempted on governance breach: {recovery_events}"
    )


def test_max_budget_governance_alert_does_not_raise():
    plane = GovernancePlane(
        limits=(MaxBudgetLimit(value=0.0000001, on_breach="alert"),),
    )
    sess = AgentSession(role="r", phase="p", governance=plane, max_iterations=10)
    with sess.session() as session:
        # First turn accrues cost > $1e-7 (pricing config makes 10 tokens ≈ $0.01)
        session.run_turn(lambda: "ok", model=_MODEL, usage=_ten_token_usage())
        # Alert mode does not raise; we should be able to do another turn
        session.run_turn(lambda: "ok", model=_MODEL, usage=_ten_token_usage())


def test_max_budget_governance_terminate_raises_after_turn():
    plane = GovernancePlane(
        limits=(MaxBudgetLimit(value=0.005, on_breach="terminate"),),
    )
    sess = AgentSession(role="r", phase="p", governance=plane, max_iterations=10)
    with pytest.raises(GovernanceBreachError) as exc_info:
        with sess.session() as session:
            # Each turn = ~$0.01 → exceeds $0.005 cap on the first turn
            session.run_turn(lambda: "ok", model=_MODEL, usage=_ten_token_usage())
    assert exc_info.value.limit_name == "max_budget_usd"


def test_max_tool_calls_governance_raises():
    plane = GovernancePlane(
        limits=(MaxToolCallsLimit(value=2, on_breach="terminate"),),
    )
    sess = AgentSession(role="r", phase="p", governance=plane)
    with pytest.raises(GovernanceBreachError) as exc_info:
        with sess.session() as session:
            for _ in range(3):
                session.run_tool("inspect", lambda: "ok")
    assert exc_info.value.limit_name == "max_tool_calls"
    assert exc_info.value.observed == 3.0


def test_consecutive_failures_governance_raises():
    plane = GovernancePlane(
        limits=(MaxConsecutiveFailuresLimit(value=2, on_breach="terminate"),),
    )
    sess = AgentSession(role="r", phase="p", governance=plane, max_iterations=10)

    def _boom() -> str:
        raise ValueError("nope")

    with pytest.raises(GovernanceBreachError) as exc_info:
        with sess.session() as session:
            for _ in range(3):
                try:
                    session.run_turn(_boom, model=_MODEL)
                except ValueError:
                    pass
    assert exc_info.value.limit_name == "max_consecutive_failures"


def test_success_resets_consecutive_failures_counter():
    plane = GovernancePlane(
        limits=(MaxConsecutiveFailuresLimit(value=2, on_breach="terminate"),),
    )
    sess = AgentSession(role="r", phase="p", governance=plane, max_iterations=10)

    def _boom() -> str:
        raise ValueError("nope")

    with sess.session() as session:
        try:
            session.run_turn(_boom, model=_MODEL)
        except ValueError:
            pass
        try:
            session.run_turn(_boom, model=_MODEL)
        except ValueError:
            pass
        # Reset via success
        session.run_turn(lambda: "ok", model=_MODEL, usage=_ten_token_usage())
        try:
            session.run_turn(_boom, model=_MODEL)
        except ValueError:
            pass
        # After reset: only 1 consecutive failure recorded, must not breach


@pytest.mark.asyncio
async def test_async_session_honors_governance():
    plane = GovernancePlane(
        limits=(MaxIterationsLimit(value=2, on_breach="terminate"),)
    )
    sess = AgentSession(role="r", phase="p", governance=plane, max_iterations=100)

    async def _coro() -> str:
        return "ok"

    with pytest.raises(GovernanceBreachError):
        async with sess.asession() as session:
            for _ in range(3):
                await session.arun_turn(_coro, model=_MODEL, usage=_ten_token_usage())


@pytest.mark.asyncio
async def test_async_tool_calls_governance():
    plane = GovernancePlane(limits=(MaxToolCallsLimit(value=1, on_breach="terminate"),))
    sess = AgentSession(role="r", phase="p", governance=plane)

    async def _ok() -> str:
        return "ok"

    with pytest.raises(GovernanceBreachError):
        async with sess.asession() as session:
            await session.arun_tool("inspect", _ok)
            await session.arun_tool("inspect", _ok)
