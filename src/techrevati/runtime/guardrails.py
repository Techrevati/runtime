"""
Guardrails — content-level checks around tool execution.

A ``Guardrail`` is a small object that inspects either the call site
(role + tool name, before invocation) or the result (after invocation)
and reports an outcome. The orchestrator runs all registered guardrails
automatically around ``run_tool`` / ``arun_tool`` and raises
``GuardrailViolatedError`` on the first violation.

This is content gating — orthogonal to ``PermissionEnforcer`` which
answers "is this role allowed to use this tool at all?". Permissions
are role × tool; guardrails are value × context.

Inspired by the OpenAI Agents SDK guardrail model. Output checks are
mandatory; input/pre-call checks are optional and default to
``GuardrailOutcome(allowed=True)`` if a guardrail does not implement
them, matching the structural Protocol pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

GuardrailStage = Literal["pre", "post"]


@dataclass(frozen=True)
class GuardrailOutcome:
    """Result of a guardrail check.

    ``allowed=False`` blocks the operation. Provide ``reason`` so the
    raised ``GuardrailViolatedError`` carries actionable context.
    """

    allowed: bool
    reason: str | None = None


@runtime_checkable
class Guardrail(Protocol):
    """Structural protocol for tool-level guardrails.

    Implementations should be small, deterministic, and side-effect-free.
    Heavy checks (e.g. calling out to a moderation model) belong behind
    a separate service the guardrail consults.

    ``name`` lets the orchestrator label events and errors; default to
    the class name if you don't override it.
    """

    name: str

    def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome: ...

    def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome: ...


class GuardrailViolatedError(Exception):
    """Raised when a guardrail blocks tool invocation or its result."""

    def __init__(
        self,
        outcome: GuardrailOutcome,
        *,
        guardrail: str,
        role: str,
        tool: str,
        stage: GuardrailStage,
    ) -> None:
        self.outcome = outcome
        self.guardrail = guardrail
        self.role = role
        self.tool = tool
        self.stage = stage
        reason = outcome.reason or "no reason provided"
        super().__init__(
            f"{stage} guardrail '{guardrail}' blocked tool '{tool}' "
            f"for role '{role}': {reason}"
        )


def run_pre_checks(guardrails: list[Guardrail], *, role: str, tool: str) -> None:
    """Run every pre-call guardrail; raise on first violation."""
    for g in guardrails:
        outcome = g.check_pre(role=role, tool=tool)
        if not outcome.allowed:
            raise GuardrailViolatedError(
                outcome,
                guardrail=getattr(g, "name", type(g).__name__),
                role=role,
                tool=tool,
                stage="pre",
            )


def run_post_checks(
    guardrails: list[Guardrail],
    value: Any,
    *,
    role: str,
    tool: str,
) -> None:
    """Run every post-call guardrail; raise on first violation."""
    for g in guardrails:
        outcome = g.check_post(value, role=role, tool=tool)
        if not outcome.allowed:
            raise GuardrailViolatedError(
                outcome,
                guardrail=getattr(g, "name", type(g).__name__),
                role=role,
                tool=tool,
                stage="post",
            )


@dataclass(frozen=True)
class AllowAllGuardrail:
    """Reference no-op guardrail. Useful as a baseline in tests."""

    name: str = "allow_all"

    def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
        return GuardrailOutcome(allowed=True)

    def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome:
        return GuardrailOutcome(allowed=True)


__all__ = [
    "AllowAllGuardrail",
    "Guardrail",
    "GuardrailOutcome",
    "GuardrailStage",
    "GuardrailViolatedError",
    "run_post_checks",
    "run_pre_checks",
]
