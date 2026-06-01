"""Tests for techrevati.runtime.guardrails (Sprint 3.4 + 3.5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from techrevati.runtime import (
    AgentFailureClass,
    AgentSession,
    AllowAllGuardrail,
    Guardrail,
    GuardrailOutcome,
    GuardrailViolatedError,
)
from techrevati.runtime.guardrails import (
    arun_post_checks,
    arun_pre_checks,
    run_post_checks,
    run_pre_checks,
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
    orch = AgentSession(role="writer", phase="draft")
    with orch.session() as session:
        assert session.run_tool("any_tool", lambda: "ok") == "ok"


def test_allow_all_guardrail_passes_through():
    orch = AgentSession(role="writer", phase="draft", guardrails=[AllowAllGuardrail()])
    with orch.session() as session:
        assert session.run_tool("any_tool", lambda: "ok") == "ok"


def test_pre_guardrail_blocks_before_invocation():
    invocations = 0

    def fn() -> str:
        nonlocal invocations
        invocations += 1
        return "ran"

    orch = AgentSession(
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
    blocked = [e for e in session.events if e.event.value == "agent.blocked"]
    assert blocked[0].data == {
        "tool": "dangerous",
        "kind": "guardrail",
        "stage": "pre",
        "guardrails": ["block_tool_pre"],
    }
    failures = [e for e in session.events if e.event.value == "agent.failed"]
    assert failures[-1].failure_class == AgentFailureClass.GUARDRAIL_VIOLATION
    assert not any(e.event.value == "agent.tool_called" for e in session.events)


def test_post_guardrail_blocks_after_invocation():
    orch = AgentSession(
        role="writer",
        phase="draft",
        guardrails=[_BlockOutputContaining(needle="secret")],
    )
    with pytest.raises(GuardrailViolatedError) as exc_info:
        with orch.session() as session:
            session.run_tool("ok_tool", lambda: "this leaks a secret")

    assert exc_info.value.stage == "post"
    assert "secret" in (exc_info.value.outcome.reason or "")
    blocked = [e for e in session.events if e.event.value == "agent.blocked"]
    assert blocked[0].data == {
        "tool": "ok_tool",
        "kind": "guardrail",
        "stage": "post",
        "guardrails": ["block_output"],
    }
    assert any(e.event.value == "agent.tool_called" for e in session.events)
    assert not any(e.event.value == "agent.tool_completed" for e in session.events)


def test_all_guardrails_run_when_first_blocks():
    """0.2.1 change: collect-all semantics so audit logs see every violation.

    Previously the first failing guardrail short-circuited; this is a
    EU AI Act Article 12 prerequisite (record-keeping must reflect the
    full set of guardrails that fired, not just the first hit).
    """

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
    orch = AgentSession(role="writer", phase="draft", guardrails=[blocking, counting])
    with pytest.raises(GuardrailViolatedError):
        with orch.session() as session:
            session.run_tool("bad", lambda: "x")

    # Counting saw the pre-call even though blocking guardrail violated;
    # the orchestrator runs every pre-check before raising.
    assert counting.calls == ["pre:bad"]


def test_multiple_simultaneous_violations_aggregated():
    """When two guardrails block at the same stage, both surface in .violations."""

    @dataclass
    class _AlwaysBlock(Guardrail):
        name: str = "always_block"

        def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
            return GuardrailOutcome(allowed=False, reason=f"{self.name} says no")

        def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome:
            return GuardrailOutcome(allowed=True)

    g1 = _AlwaysBlock(name="g1")
    g2 = _AlwaysBlock(name="g2")
    orch = AgentSession(role="writer", phase="draft", guardrails=[g1, g2])

    with pytest.raises(GuardrailViolatedError) as exc_info:
        with orch.session() as session:
            session.run_tool("anything", lambda: "x")

    err = exc_info.value
    assert len(err.violations) == 2
    names = {v.guardrail for v in err.violations}
    assert names == {"g1", "g2"}
    # First-violation mirror still works for legacy callers
    assert err.outcome is err.violations[0].outcome
    assert err.guardrail == err.violations[0].guardrail
    assert err.stage == "pre"


@pytest.mark.asyncio
async def test_async_run_tool_runs_guardrails():
    orch = AgentSession(
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
    blocked = [e for e in session.events if e.event.value == "agent.blocked"]
    assert blocked[0].data == {
        "tool": "g",
        "kind": "guardrail",
        "stage": "post",
        "guardrails": ["block_output"],
    }


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


def test_guardrail_runners_validate_role_tool_and_outcome_shape():
    class _BadOutcome:
        name = "bad_outcome"

        def check_pre(self, *, role: str, tool: str) -> Any:
            return object()

        def check_post(self, value: Any, *, role: str, tool: str) -> Any:
            return object()

    good = AllowAllGuardrail()
    with pytest.raises(ValueError, match="role"):
        run_pre_checks([good], role="", tool="tool")
    with pytest.raises(ValueError, match="tool"):
        run_post_checks([good], "value", role="role", tool=" ")
    with pytest.raises(TypeError, match="GuardrailOutcome"):
        run_pre_checks([cast(Guardrail, _BadOutcome())], role="role", tool="tool")
    with pytest.raises(TypeError, match="GuardrailOutcome"):
        run_post_checks(
            [cast(Guardrail, _BadOutcome())],
            "value",
            role="role",
            tool="tool",
        )


def test_violated_error_rejects_invalid_shape():
    with pytest.raises(ValueError, match="role"):
        GuardrailViolatedError(
            GuardrailOutcome(allowed=False),
            guardrail="g1",
            role="",
            tool="tool",
            stage="pre",
        )
    with pytest.raises(TypeError, match="GuardrailViolation"):
        GuardrailViolatedError(
            cast(Any, (object(),)),
            role="role",
            tool="tool",
        )


@pytest.mark.asyncio
async def test_async_guardrail_runners_validate_role_tool_and_outcome_shape():
    class _BadAsyncOutcome:
        name = "bad_async_outcome"

        async def acheck_pre(self, *, role: str, tool: str) -> Any:
            return object()

        async def acheck_post(self, value: Any, *, role: str, tool: str) -> Any:
            return object()

    good = AllowAllGuardrail()
    with pytest.raises(ValueError, match="role"):
        await arun_pre_checks([good], role="", tool="tool")
    with pytest.raises(ValueError, match="tool"):
        await arun_post_checks([good], "value", role="role", tool=" ")
    with pytest.raises(TypeError, match="GuardrailOutcome"):
        await arun_pre_checks(
            [cast(Any, _BadAsyncOutcome())],
            role="role",
            tool="tool",
        )
    with pytest.raises(TypeError, match="GuardrailOutcome"):
        await arun_post_checks(
            [cast(Any, _BadAsyncOutcome())],
            "value",
            role="role",
            tool="tool",
        )
