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


@dataclass(frozen=True)
class GuardrailViolation:
    """One violation entry in a ``GuardrailViolatedError``.

    A single tool invocation can violate multiple guardrails at the
    same stage; the orchestrator collects them all before raising so
    that audit logs (EU AI Act Article 12 record-keeping) see the full
    picture instead of just the first hit.
    """

    outcome: GuardrailOutcome
    guardrail: str
    stage: GuardrailStage

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": {"allowed": self.outcome.allowed, "reason": self.outcome.reason},
            "guardrail": self.guardrail,
            "stage": self.stage,
        }


class GuardrailViolatedError(Exception):
    """Raised when one or more guardrails block tool invocation or its result.

    Carries a tuple of ``violations`` (every guardrail that blocked at
    the same stage). The single-violation attributes ``outcome``,
    ``guardrail``, and ``stage`` mirror the first violation so existing
    handlers that read them keep working unchanged.
    """

    def __init__(
        self,
        violations: tuple[GuardrailViolation, ...] | GuardrailOutcome,
        *,
        guardrail: str | None = None,
        role: str,
        tool: str,
        stage: GuardrailStage | None = None,
    ) -> None:
        # Backward-compatible positional shape: (outcome, guardrail=..., stage=...)
        if isinstance(violations, GuardrailOutcome):
            assert guardrail is not None and stage is not None, (
                "legacy single-outcome construction requires guardrail and stage"
            )
            violations = (
                GuardrailViolation(
                    outcome=violations, guardrail=guardrail, stage=stage
                ),
            )

        if not violations:
            raise ValueError("GuardrailViolatedError requires at least one violation")

        self.violations: tuple[GuardrailViolation, ...] = tuple(violations)
        self.role = role
        self.tool = tool

        # Mirror the first violation onto top-level attributes for
        # 0.2.0-era callers that read `error.outcome` / `error.guardrail`
        # / `error.stage` directly.
        first = self.violations[0]
        self.outcome = first.outcome
        self.guardrail = first.guardrail
        self.stage = first.stage

        if len(self.violations) == 1:
            reason = first.outcome.reason or "no reason provided"
            message = (
                f"{first.stage} guardrail '{first.guardrail}' blocked tool "
                f"'{tool}' for role '{role}': {reason}"
            )
        else:
            names = ", ".join(v.guardrail for v in self.violations)
            message = (
                f"{first.stage} stage: {len(self.violations)} guardrails "
                f"blocked tool '{tool}' for role '{role}': {names}"
            )
        super().__init__(message)


def run_pre_checks(guardrails: list[Guardrail], *, role: str, tool: str) -> None:
    """Run every pre-call guardrail; raise once with all violations collected."""
    violations: list[GuardrailViolation] = []
    for g in guardrails:
        outcome = g.check_pre(role=role, tool=tool)
        if not outcome.allowed:
            violations.append(
                GuardrailViolation(
                    outcome=outcome,
                    guardrail=getattr(g, "name", type(g).__name__),
                    stage="pre",
                )
            )
    if violations:
        raise GuardrailViolatedError(tuple(violations), role=role, tool=tool)


def run_post_checks(
    guardrails: list[Guardrail],
    value: Any,
    *,
    role: str,
    tool: str,
) -> None:
    """Run every post-call guardrail; raise once with all violations collected."""
    violations: list[GuardrailViolation] = []
    for g in guardrails:
        outcome = g.check_post(value, role=role, tool=tool)
        if not outcome.allowed:
            violations.append(
                GuardrailViolation(
                    outcome=outcome,
                    guardrail=getattr(g, "name", type(g).__name__),
                    stage="post",
                )
            )
    if violations:
        raise GuardrailViolatedError(tuple(violations), role=role, tool=tool)


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
    "GuardrailViolation",
    "run_post_checks",
    "run_pre_checks",
]
