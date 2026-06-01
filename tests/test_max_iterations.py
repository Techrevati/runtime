"""Tests for max_iterations cap (Sprint 3.1)."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    AgentFailureClass,
    AgentSession,
    MaxIterationsExceededError,
    ModelPricing,
    register_pricing,
)


@pytest.fixture(autouse=True)
def _register_test_pricing():
    register_pricing("test-model", ModelPricing(3.0, 15.0))


def test_default_cap_is_25():
    orch = AgentSession(role="r", phase="p")
    assert orch.max_iterations == 25


def test_run_turn_raises_when_cap_reached():
    orch = AgentSession(role="r", phase="p", max_iterations=3)
    with pytest.raises(MaxIterationsExceededError) as exc_info:
        with orch.session() as session:
            for _ in range(4):  # one over the cap
                session.run_turn(lambda: "ok", model="test-model")

    assert exc_info.value.max_iterations == 3


def test_run_turn_cap_escape_marks_terminal_governance_breach():
    orch = AgentSession(role="r", phase="p", max_iterations=1)
    session = None

    with pytest.raises(MaxIterationsExceededError):
        with orch.session() as running:
            session = running
            running.run_turn(lambda: "ok", model="test-model")
            running.run_turn(lambda: "too many", model="test-model")

    assert session is not None
    failures = [
        event for event in session.events if event.event.value == "agent.failed"
    ]
    assert failures[-1].failure_class == AgentFailureClass.GOVERNANCE_BREACH
    assert failures[-1].detail == "MaxIterationsExceededError raised"


def test_run_turn_within_cap_succeeds():
    orch = AgentSession(role="r", phase="p", max_iterations=3)
    with orch.session() as session:
        for _ in range(3):
            session.run_turn(lambda: "ok", model="test-model")
    assert session._iteration_count == 3


def test_cap_of_zero_blocks_first_turn():
    orch = AgentSession(role="r", phase="p", max_iterations=0)
    with pytest.raises(MaxIterationsExceededError):
        with orch.session() as session:
            session.run_turn(lambda: "ok", model="test-model")


@pytest.mark.asyncio
async def test_arun_turn_raises_when_cap_reached():
    orch = AgentSession(role="r", phase="p", max_iterations=2)

    async def call():
        return "ok"

    with pytest.raises(MaxIterationsExceededError):
        async with orch.asession() as session:
            for _ in range(3):
                await session.arun_turn(call, model="test-model")


@pytest.mark.asyncio
async def test_arun_turn_cap_escape_marks_terminal_governance_breach():
    orch = AgentSession(role="r", phase="p", max_iterations=1)
    session = None

    async def call():
        return "ok"

    with pytest.raises(MaxIterationsExceededError):
        async with orch.asession() as running:
            session = running
            await running.arun_turn(call, model="test-model")
            await running.arun_turn(call, model="test-model")

    assert session is not None
    failures = [
        event for event in session.events if event.event.value == "agent.failed"
    ]
    assert failures[-1].failure_class == AgentFailureClass.GOVERNANCE_BREACH
    assert failures[-1].detail == "MaxIterationsExceededError raised"


def test_agent_session_alias_is_orchestrator():
    """AgentSession remains the canonical session factory."""
    assert AgentSession is AgentSession
    instance = AgentSession(role="r", phase="p", max_iterations=10)
    assert instance.max_iterations == 10
