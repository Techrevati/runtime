"""Direct unit tests for the extracted exception-classification helpers."""

from __future__ import annotations

from techrevati.runtime._classify import (
    _is_prompt_rejection_exception,
    _safe_exception_detail,
    _scenario_to_class,
)
from techrevati.runtime.agent_events import AgentFailureClass
from techrevati.runtime.retry_policy import FailureScenario


def test_scenario_to_class_maps_every_scenario() -> None:
    # Every known scenario maps to a concrete (non-UNKNOWN) failure class.
    for scenario in FailureScenario:
        assert _scenario_to_class(scenario) is not AgentFailureClass.UNKNOWN
    assert _scenario_to_class(FailureScenario.LLM_TIMEOUT) is (
        AgentFailureClass.LLM_TIMEOUT
    )
    assert _scenario_to_class(FailureScenario.PROVIDER_FAILURE) is (
        AgentFailureClass.DEPENDENCY_FAILED
    )


def test_prompt_rejection_direct_marker() -> None:
    assert _is_prompt_rejection_exception(ValueError("Content Policy violation"))
    assert not _is_prompt_rejection_exception(ValueError("just a normal error"))


def test_prompt_rejection_follows_cause_chain() -> None:
    try:
        try:
            raise RuntimeError("content filter triggered")
        except RuntimeError as inner:
            raise ValueError("wrapper") from inner
    except ValueError as exc:
        assert _is_prompt_rejection_exception(exc)


def test_prompt_rejection_follows_implicit_context() -> None:
    try:
        try:
            raise RuntimeError("moderation blocked the request")
        except RuntimeError:
            raise ValueError("wrapper")  # noqa: B904 — implicit __context__ is the point
    except ValueError as exc:
        assert _is_prompt_rejection_exception(exc)


def test_prompt_rejection_respects_suppressed_context() -> None:
    try:
        try:
            raise RuntimeError("jailbreak detected")
        except RuntimeError:
            raise ValueError("clean") from None  # suppresses __context__
    except ValueError as exc:
        assert not _is_prompt_rejection_exception(exc)


def test_prompt_rejection_terminates_on_cyclic_chain() -> None:
    a = ValueError("a")
    b = ValueError("b")
    a.__cause__ = b
    b.__cause__ = a  # cycle — the seen-set guard must terminate
    assert _is_prompt_rejection_exception(a) is False


def test_safe_exception_detail_omits_message() -> None:
    detail = _safe_exception_detail(ValueError("super secret PII"))
    assert detail == "ValueError raised"
    assert "secret" not in detail
