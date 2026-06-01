"""
Policy Engine — Declarative rules for orchestration decisions.

Composable conditions (And/Or/QualityAt/...) evaluated against a
PhaseContext, producing a flat list of PolicyActionData for the
caller to dispatch. Rules are sorted by priority (lower first);
all matching rules fire.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from techrevati.runtime.quality_gate import QualityLevel

logger = logging.getLogger("techrevati.runtime.policy")
logger.addHandler(logging.NullHandler())


def _validate_finite_amount(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    if amount < 0:
        raise ValueError(f"{name} must be >= 0")
    return amount


def _validate_optional_name(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string or None")
    return value.strip()


def _validate_quality_level(level: QualityLevel) -> QualityLevel:
    if isinstance(level, bool):
        raise TypeError("quality level must be a QualityLevel")
    try:
        return QualityLevel(level)
    except ValueError as exc:
        raise ValueError("quality level must be a valid QualityLevel") from exc


def _copy_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    if not isinstance(params, dict):
        raise TypeError("params must be a dict or None")
    copied = deepcopy(params)
    for key in copied:
        if not isinstance(key, str):
            raise TypeError("params keys must be strings")
    return copied


def _copy_action(action: PolicyActionData) -> PolicyActionData:
    if not isinstance(action, PolicyActionData):
        raise TypeError("actions must contain PolicyActionData instances")
    return PolicyActionData(action.action, action.params)


def _copy_rule(rule: PolicyRule) -> PolicyRule:
    if not isinstance(rule, PolicyRule):
        raise TypeError("rules must contain PolicyRule instances")
    return PolicyRule(
        name=rule.name,
        condition=rule.condition,
        actions=tuple(_copy_action(action) for action in rule.actions),
        priority=rule.priority,
    )


def _validate_priority(priority: int) -> int:
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise TypeError("priority must be an integer")
    return priority


def _validate_optional_label(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if value and not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _copy_name_set(field_name: str, value: set[str]) -> set[str]:
    if not isinstance(value, set):
        raise TypeError(f"{field_name} must be a set")
    copied = set(value)
    for item in copied:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} values must be non-empty strings")
    return {item.strip() for item in copied}


# -- Conditions --


class PolicyCondition(ABC):
    """Base class for policy conditions. Override matches()."""

    @abstractmethod
    def matches(self, ctx: PhaseContext) -> bool:
        """Return whether the condition matches the current phase context."""
        ...


class And(PolicyCondition):
    """All conditions must match."""

    def __init__(self, conditions: list[PolicyCondition]) -> None:
        self.conditions = conditions

    def matches(self, ctx: PhaseContext) -> bool:
        if not self.conditions:
            return True
        return all(c.matches(ctx) for c in self.conditions)

    def __repr__(self) -> str:
        return f"And({self.conditions})"


class Or(PolicyCondition):
    """Any condition must match."""

    def __init__(self, conditions: list[PolicyCondition]) -> None:
        self.conditions = conditions

    def matches(self, ctx: PhaseContext) -> bool:
        if not self.conditions:
            return False
        return any(c.matches(ctx) for c in self.conditions)

    def __repr__(self) -> str:
        return f"Or({self.conditions})"


class QualityAt(PolicyCondition):
    """Matches if observed quality level >= required."""

    def __init__(self, level: QualityLevel) -> None:
        self.level = _validate_quality_level(level)

    def matches(self, ctx: PhaseContext) -> bool:
        if ctx.quality_level is None:
            return False
        return ctx.quality_level >= self.level

    def __repr__(self) -> str:
        return f"QualityAt({self.level.name})"


class PhaseCompleted(PolicyCondition):
    def matches(self, ctx: PhaseContext) -> bool:
        return ctx.phase_completed

    def __repr__(self) -> str:
        return "PhaseCompleted()"


class AgentFailed(PolicyCondition):
    """Matches if any agent (or a specific role) failed."""

    def __init__(self, role: str | None = None) -> None:
        self.role = _validate_optional_name("role", role)

    def matches(self, ctx: PhaseContext) -> bool:
        if self.role:
            return self.role in ctx.failed_roles
        return len(ctx.failed_roles) > 0

    def __repr__(self) -> str:
        return f"AgentFailed(role={self.role!r})"


class GateBelow(PolicyCondition):
    """Matches if gate score is below threshold."""

    def __init__(self, threshold: float) -> None:
        self.threshold = _validate_finite_amount("threshold", threshold)

    def matches(self, ctx: PhaseContext) -> bool:
        return ctx.gate_score < self.threshold

    def __repr__(self) -> str:
        return f"GateBelow({self.threshold})"


class RetryExhausted(PolicyCondition):
    """Matches if recovery retries are exhausted for a scenario."""

    def __init__(self, scenario: str | None = None) -> None:
        self.scenario = _validate_optional_name("scenario", scenario)

    def matches(self, ctx: PhaseContext) -> bool:
        if self.scenario:
            return self.scenario in ctx.retry_exhausted_scenarios
        return len(ctx.retry_exhausted_scenarios) > 0

    def __repr__(self) -> str:
        return f"RetryExhausted(scenario={self.scenario!r})"


class TimedOut(PolicyCondition):
    """Matches if elapsed time exceeds duration."""

    def __init__(self, seconds: float) -> None:
        self.seconds = _validate_finite_amount("seconds", seconds)

    def matches(self, ctx: PhaseContext) -> bool:
        return ctx.elapsed_seconds > self.seconds

    def __repr__(self) -> str:
        return f"TimedOut({self.seconds}s)"


class AllAgentsComplete(PolicyCondition):
    def matches(self, ctx: PhaseContext) -> bool:
        return bool(ctx.all_roles) and (
            ctx.completed_roles | ctx.failed_roles == ctx.all_roles
        )

    def __repr__(self) -> str:
        return "AllAgentsComplete()"


class CostExceeded(PolicyCondition):
    """Matches if total cost exceeds budget."""

    def __init__(self, budget_usd: float) -> None:
        self.budget_usd = _validate_finite_amount("budget_usd", budget_usd)

    def matches(self, ctx: PhaseContext) -> bool:
        return ctx.total_cost_usd > self.budget_usd

    def __repr__(self) -> str:
        return f"CostExceeded(${self.budget_usd})"


# -- Actions --


class PolicyAction(str, Enum):
    """Actions a rule can recommend. The caller is responsible for dispatch."""

    ADVANCE_PHASE = "advance_phase"
    RETRY_AGENT = "retry_agent"
    RETRY_PHASE = "retry_phase"
    RECOVER_ONCE = "recover_once"
    ESCALATE = "escalate"
    STORE_GATE_FEEDBACK = "store_gate_feedback"
    GENERATE_HANDOFF = "generate_handoff"
    NOTIFY = "notify"
    ABORT_PHASE = "abort_phase"


@dataclass(frozen=True)
class PolicyActionData:
    """Action with optional parameters."""

    action: PolicyAction
    params: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        try:
            action = PolicyAction(self.action)
        except ValueError as exc:
            raise ValueError("action must be a valid PolicyAction") from exc
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "params", _copy_params(self.params))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.action.value}
        if self.params:
            d["params"] = deepcopy(self.params)
        return d


@dataclass(frozen=True)
class PolicyRule:
    """A named rule with condition, action(s), and priority."""

    name: str
    condition: PolicyCondition
    actions: Sequence[PolicyActionData]
    priority: int = 50  # lower = higher priority

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("policy rule name must be a non-empty string")
        if not callable(getattr(self.condition, "matches", None)):
            raise ValueError("condition must expose a callable matches(ctx)")
        if isinstance(self.actions, (str, bytes)) or not isinstance(
            self.actions, Sequence
        ):
            raise TypeError("policy rule actions must be a sequence")
        actions = tuple(_copy_action(action) for action in self.actions)
        if not actions:
            raise ValueError("policy rule actions must not be empty")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "priority", _validate_priority(self.priority))


# -- Context --


@dataclass
class PhaseContext:
    """Input to policy evaluation. Captures current phase state."""

    phase: str = ""
    quality_level: QualityLevel | None = None
    gate_score: float = 0.0
    gate_threshold: float = 0.0
    completed_roles: set[str] = field(default_factory=set)
    failed_roles: set[str] = field(default_factory=set)
    all_roles: set[str] = field(default_factory=set)
    elapsed_seconds: float = 0.0
    retry_exhausted_scenarios: set[str] = field(default_factory=set)
    phase_completed: bool = False
    total_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        self.phase = _validate_optional_label("phase", self.phase)
        if self.quality_level is not None:
            self.quality_level = _validate_quality_level(self.quality_level)
        for field_name in (
            "gate_score",
            "gate_threshold",
            "elapsed_seconds",
            "total_cost_usd",
        ):
            setattr(
                self,
                field_name,
                _validate_finite_amount(field_name, getattr(self, field_name)),
            )
        self.completed_roles = _copy_name_set("completed_roles", self.completed_roles)
        self.failed_roles = _copy_name_set("failed_roles", self.failed_roles)
        self.all_roles = _copy_name_set("all_roles", self.all_roles)
        self.retry_exhausted_scenarios = _copy_name_set(
            "retry_exhausted_scenarios", self.retry_exhausted_scenarios
        )
        if not isinstance(self.phase_completed, bool):
            raise TypeError("phase_completed must be a bool")


# -- Engine --


class PolicyEngine:
    """Evaluates rules against a PhaseContext."""

    def __init__(self, rules: Sequence[PolicyRule]) -> None:
        if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence):
            raise TypeError("rules must be a sequence")
        self._rules = tuple(
            sorted((_copy_rule(rule) for rule in rules), key=lambda r: r.priority)
        )

    def evaluate(self, ctx: PhaseContext) -> list[PolicyActionData]:
        """Evaluate all rules. Returns the flat list of matching actions."""
        actions: list[PolicyActionData] = []
        for rule in self._rules:
            if rule.condition.matches(ctx):
                actions.extend(_copy_action(action) for action in rule.actions)
        return actions

    async def evaluate_async(self, ctx: PhaseContext) -> list[PolicyActionData]:
        """Async sibling of ``evaluate``.

        Conditions whose ``matches`` is a coroutine function are awaited;
        sync conditions are called in place. Falls back to the sync path
        if every rule is sync, so existing engines run unchanged.
        """
        import inspect

        actions: list[PolicyActionData] = []
        for rule in self._rules:
            match_fn = rule.condition.matches
            result = match_fn(ctx)
            if inspect.isawaitable(result):
                ok = bool(await result)
            else:
                ok = bool(result)
            if ok:
                actions.extend(_copy_action(action) for action in rule.actions)
        return actions

    @property
    def rules(self) -> list[PolicyRule]:
        return [_copy_rule(rule) for rule in self._rules]
