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

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)
_ASYNC_IN_SYNC_WARNED: set[int] = set()


def _maybe_warn_async_in_sync(g: object) -> None:
    """Emit a one-shot logger warning when an AsyncGuardrail leaks into a sync path."""
    if id(g) in _ASYNC_IN_SYNC_WARNED:
        return
    _ASYNC_IN_SYNC_WARNED.add(id(g))
    logger.warning(
        "AsyncGuardrail %r seen in sync run_tool path; skipping. Use "
        "AsyncOrchestrationSession.arun_tool to honor async guardrails.",
        getattr(g, "name", type(g).__name__),
    )


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


@runtime_checkable
class AsyncGuardrail(Protocol):
    """Async sibling of ``Guardrail`` — for checks that need I/O.

    When a heavy guardrail must call out to a moderation model, a vector
    store, or another service over the network, sync ``Guardrail``
    would block the event loop. ``AsyncGuardrail`` lets the check be
    awaited.

    ``AsyncOrchestrationSession.arun_tool`` accepts a mixed list of
    sync and async guardrails: it detects ``AsyncGuardrail`` instances
    via ``isinstance`` and awaits them; sync ``Guardrail`` instances
    run synchronously in place. Sync sessions silently skip
    ``AsyncGuardrail`` instances (with a one-shot logger warning) since
    there's no event loop to await on.
    """

    name: str

    async def acheck_pre(self, *, role: str, tool: str) -> GuardrailOutcome: ...

    async def acheck_post(
        self, value: Any, *, role: str, tool: str
    ) -> GuardrailOutcome: ...


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


def run_pre_checks(
    guardrails: list[Guardrail] | list[Guardrail | AsyncGuardrail],
    *,
    role: str,
    tool: str,
) -> None:
    """Run every pre-call guardrail; raise once with all violations collected.

    ``AsyncGuardrail`` instances are skipped with a one-shot logger warning
    (sync path has no event loop to await on).
    """
    violations: list[GuardrailViolation] = []
    for g in guardrails:
        if isinstance(g, AsyncGuardrail) and not isinstance(g, Guardrail):
            _maybe_warn_async_in_sync(g)
            continue
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
    guardrails: list[Guardrail] | list[Guardrail | AsyncGuardrail],
    value: Any,
    *,
    role: str,
    tool: str,
) -> None:
    """Run every post-call guardrail; raise once with all violations collected.

    ``AsyncGuardrail`` instances are skipped with a one-shot logger warning.
    """
    violations: list[GuardrailViolation] = []
    for g in guardrails:
        if isinstance(g, AsyncGuardrail) and not isinstance(g, Guardrail):
            _maybe_warn_async_in_sync(g)
            continue
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


async def arun_pre_checks(
    guardrails: list[Guardrail | AsyncGuardrail],
    *,
    role: str,
    tool: str,
) -> None:
    """Run every pre-call guardrail; await async ones, call sync ones inline."""
    violations: list[GuardrailViolation] = []
    for g in guardrails:
        if isinstance(g, AsyncGuardrail):
            outcome = await g.acheck_pre(role=role, tool=tool)
        else:
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


async def arun_post_checks(
    guardrails: list[Guardrail | AsyncGuardrail],
    value: Any,
    *,
    role: str,
    tool: str,
) -> None:
    """Run every post-call guardrail; await async ones, call sync ones inline."""
    violations: list[GuardrailViolation] = []
    for g in guardrails:
        if isinstance(g, AsyncGuardrail):
            outcome = await g.acheck_post(value, role=role, tool=tool)
        else:
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


class PatternGuardrail:
    """Regex deny-list guardrail. Sub-200ms per check for ~100 patterns.

    Composes one compiled regex from the deny-list (alternation) so a
    check is one regex search, not N searches. ``stages`` selects which
    side of the tool call to gate; pass ``("pre", "post")`` for both.

    Used standalone for caller-defined deny-lists (e.g., "block any
    tool name matching ``rm.*``") and as the substrate for
    ``PromptInjectionGuardrail`` below.
    """

    def __init__(
        self,
        deny_patterns: list[str],
        *,
        stages: tuple[GuardrailStage, ...] = ("pre", "post"),
        name: str = "pattern",
        flags: int = re.IGNORECASE,
    ) -> None:
        if not deny_patterns:
            raise ValueError("PatternGuardrail requires at least one deny pattern")
        self.name = name
        self._stages = frozenset(stages)
        # One compiled regex via alternation; groups stripped to keep it cheap.
        self._regex = re.compile(
            "|".join(f"(?:{p})" for p in deny_patterns),
            flags=flags,
        )
        self._deny_patterns = tuple(deny_patterns)

    @property
    def deny_patterns(self) -> tuple[str, ...]:
        return self._deny_patterns

    def _evaluate(self, haystack: str) -> GuardrailOutcome:
        m = self._regex.search(haystack)
        if m is None:
            return GuardrailOutcome(allowed=True)
        return GuardrailOutcome(
            allowed=False,
            reason=f"matched deny pattern at position {m.start()}: {m.group(0)!r}",
        )

    def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
        if "pre" not in self._stages:
            return GuardrailOutcome(allowed=True)
        return self._evaluate(tool)

    def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome:
        if "post" not in self._stages:
            return GuardrailOutcome(allowed=True)
        return self._evaluate(str(value))


# Canonical prompt-injection signatures. Heuristic, NOT a replacement for
# a specialized moderation model; documented as "first line of defense".
# Cited families:
#   - role hijack ("you are now", "act as")
#   - direct instruction override ("ignore previous", "forget your")
#   - delimiter abuse (triple backticks following "system:")
#   - long base64 / hex blobs (smuggled payloads)
_PROMPT_INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore\s+(?:all\s+|the\s+)?previous\s+instructions",
    r"disregard\s+(?:all\s+|the\s+)?previous\s+(?:instructions|messages|context)",
    r"forget\s+(?:everything|all|your)\b",
    r"(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you))\s+\w+",
    r"system\s*[:>]\s*```",
    r"<\s*\|?\s*(?:system|admin|root)\s*\|?\s*>",
    r"\b(?:override|bypass|disable)\s+(?:the\s+|a\s+)?"
    r"(?:safety|guardrail|filter|policy|safeguard)",
    r"reveal\s+(?:your|the)\s+(?:system\s+prompt|instructions|initial\s+prompt)",
    r"(?:show|tell)\s+me\s+(?:your|the)\s+"
    r"(?:system\s+prompt|instructions|initial\s+prompt)",
    r"\binstructions\s+you\s+(?:were|have\s+been)\s+given\b",
    r"[A-Za-z0-9+/=]{200,}",  # large base64-ish blob
)


class PromptInjectionGuardrail(PatternGuardrail):
    """First-line heuristic prompt-injection detector. Zero deps.

    Specialization of ``PatternGuardrail`` with a built-in list of
    canonical prompt-injection signatures. Documented as a *first line
    of defense*, not a replacement for a specialized moderation model:
    sophisticated attackers will defeat this. Pair with a model-backed
    moderation guardrail behind the same orchestrator for layered
    defense.

    Default ``stages=("post",)`` catches injections in tool *outputs*
    (the most common indirect-injection vector — malicious content
    retrieved from RAG, scraped pages, etc.). Add ``"pre"`` to also
    scrutinize tool names.

    Mirrors EU AI Act Article 15 cybersecurity expectations for
    "resilience against attempts by unauthorised third parties to alter
    [an AI system's] use, outputs or performance".
    """

    def __init__(
        self,
        *,
        stages: tuple[GuardrailStage, ...] = ("post",),
        extra_patterns: tuple[str, ...] = (),
        name: str = "prompt_injection",
    ) -> None:
        super().__init__(
            list(_PROMPT_INJECTION_PATTERNS) + list(extra_patterns),
            stages=stages,
            name=name,
        )


__all__ = [
    "AllowAllGuardrail",
    "AsyncGuardrail",
    "Guardrail",
    "GuardrailOutcome",
    "GuardrailStage",
    "GuardrailViolatedError",
    "GuardrailViolation",
    "PatternGuardrail",
    "PromptInjectionGuardrail",
    "arun_post_checks",
    "arun_pre_checks",
    "run_post_checks",
    "run_pre_checks",
]
