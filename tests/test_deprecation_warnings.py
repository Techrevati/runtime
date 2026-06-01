"""Verify the 0.3.x compatibility deprecation surface."""

from __future__ import annotations

import warnings

import pytest

from techrevati.runtime import AgentSession
from techrevati.runtime.orchestrator import Orchestrator


def test_orchestrator_alias_emits_deprecation_warning_on_first_instantiation() -> None:
    Orchestrator._deprecation_emitted = False
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", DeprecationWarning)
        Orchestrator(role="writer", phase="draft")

    deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1
    message = str(deprecations[0].message)
    assert "Orchestrator" in message
    assert "AgentSession" in message
    assert "0.4.0" in message


def test_orchestrator_alias_only_warns_once_per_process() -> None:
    Orchestrator._deprecation_emitted = False
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", DeprecationWarning)
        Orchestrator(role="writer", phase="draft")
        Orchestrator(role="writer", phase="draft")
        Orchestrator(role="writer", phase="draft")

    deprecations = [w for w in captured if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1


def test_orchestrator_alias_is_subclass_of_agent_session() -> None:
    Orchestrator._deprecation_emitted = True  # silence
    instance = Orchestrator(role="writer", phase="draft")
    assert isinstance(instance, AgentSession)


def test_orchestrator_alias_forwards_all_kwargs_to_agent_session() -> None:
    Orchestrator._deprecation_emitted = True
    instance = Orchestrator(
        role="reviewer",
        phase="final",
        project_id=42,
        budget_usd=10.0,
        enforce_budget=True,
        max_iterations=7,
    )
    assert instance.role == "reviewer"
    assert instance.phase == "final"
    assert instance.project_id == 42
    assert instance.budget_usd == 10.0
    assert instance.enforce_budget is True
    assert instance.max_iterations == 7


@pytest.fixture(autouse=True)
def _reset_deprecation_flag() -> None:
    """Each test starts with a fresh deprecation flag."""
    Orchestrator._deprecation_emitted = False
