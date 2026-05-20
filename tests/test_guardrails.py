"""Tests for techrevati.runtime.guardrails (Sprint 3.4 + 3.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from techrevati.runtime import (
    AllowAllGuardrail,
    Guardrail,
    GuardrailOutcome,
    GuardrailViolatedError,
    Orchestrator,
)


@dataclass
class _BlockToolPre(Guardrail):
    """Blocks a specific tool name pre-call."""

    blocked: str
    name: str = "block_tool_pre"

    def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
        if tool == self.blocked:
            return GuardrailOutcome(allowed=False, reason=f"{tool} is forbidden")
        return GuardrailOutcome(allowed=True)

    def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome:
        return GuardrailOutcome(allowed=True)


@dataclass
class _BlockOutputContaining(Guardrail):
    """Blocks any output whose stringified form contains a substring."""

    needle: str
    name: str = "block_output"

    def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
        return GuardrailOutcome(allowed=True)

    def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome:
        if self.needle in str(value):
            return GuardrailOutcome(
                allowed=False, reason=f"output contained '{self.needle}'"
            )
        return GuardrailOutcome(allowed=True)


def test_no_guardrails_means_no_checks():
    orch = Orchestrator(role="writer", phase="draft")
    with orch.session() as session:
        assert session.run_tool("any_tool", lambda: "ok") == "ok"


def test_allow_all_guardrail_passes_through():
    orch = Orchestrator(role="writer", phase="draft", guardrails=[AllowAllGuardrail()])
    with orch.session() as session:
        assert session.run_tool("any_tool", lambda: "ok") == "ok"


def test_pre_guardrail_blocks_before_invocation():
    invocations = 0

    def fn() -> str:
        nonlocal invocations
        invocations += 1
        return "ran"

    orch = Orchestrator(
        role="writer",
        phase="draft",
        guardrails=[_BlockToolPre(blocked="dangerous")],
    )
    with pytest.raises(GuardrailViolatedError) as exc_info:
        with orch.session() as session:
            session.run_tool("dangerous", fn)

    assert exc_info.value.stage == "pre"
    assert exc_info.value.tool == "dangerous"
    assert invocations == 0  # never ran


def test_post_guardrail_blocks_after_invocation():
    orch = Orchestrator(
        role="writer",
        phase="draft",
        guardrails=[_BlockOutputContaining(needle="secret")],
    )
    with pytest.raises(GuardrailViolatedError) as exc_info:
        with orch.session() as session:
            session.run_tool("ok_tool", lambda: "this leaks a secret")

    assert exc_info.value.stage == "post"
    assert "secret" in (exc_info.value.outcome.reason or "")


def test_first_violating_guardrail_short_circuits():
    """Multiple guardrails: first failure stops the chain."""

    @dataclass
    class _Counting(Guardrail):
        name: str = "count"
        calls: list[str] = None  # type: ignore[assignment]

        def __post_init__(self) -> None:
            if self.calls is None:
                self.calls = []

        def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
            self.calls.append(f"pre:{tool}")
            return GuardrailOutcome(allowed=True)

        def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome:
            self.calls.append(f"post:{tool}")
            return GuardrailOutcome(allowed=True)

    blocking = _BlockToolPre(blocked="bad")
    counting = _Counting()
    orch = Orchestrator(role="writer", phase="draft", guardrails=[blocking, counting])
    with pytest.raises(GuardrailViolatedError):
        with orch.session() as session:
            session.run_tool("bad", lambda: "x")

    # Counting never saw the pre-call because blocking guardrail short-circuited.
    assert counting.calls == []


@pytest.mark.asyncio
async def test_async_run_tool_runs_guardrails():
    orch = Orchestrator(
        role="writer",
        phase="draft",
        guardrails=[_BlockOutputContaining(needle="leak")],
    )

    async def good():
        return "fine"

    async def bad():
        return "this is a leak"

    async with orch.asession() as session:
        assert await session.arun_tool("g", good) == "fine"

    with pytest.raises(GuardrailViolatedError):
        async with orch.asession() as session:
            await session.arun_tool("g", bad)


def test_violated_error_carries_context():
    err = GuardrailViolatedError(
        GuardrailOutcome(allowed=False, reason="why"),
        guardrail="g1",
        role="r",
        tool="t",
        stage="pre",
    )
    assert err.role == "r"
    assert err.tool == "t"
    assert err.stage == "pre"
    assert err.guardrail == "g1"
    assert "why" in str(err)
