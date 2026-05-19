"""
Policy Engine — Declarative rules for orchestration decisions.

Composable conditions (And/Or/QualityAt/...) evaluated against a
PhaseContext, producing a flat list of PolicyActionData for the
caller to dispatch. Rules are sorted by priority (lower first);
all matching rules fire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from techrevati.runtime.quality_gate import QualityLevel

logger = logging.getLogger("techrevati.runtime.policy")
logger.addHandler(logging.NullHandler())


# -- Conditions --


class PolicyCondition:
    """Base class for policy conditions. Override matches()."""

    def matches(self, ctx: PhaseContext) -> bool:
        raise NotImplementedError


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
        self.level = level

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
        self.role = role

    def matches(self, ctx: PhaseContext) -> bool:
        if self.role:
            return self.role in ctx.failed_roles
        return len(ctx.failed_roles) > 0

    def __repr__(self) -> str:
        return f"AgentFailed(role={self.role!r})"


class GateBelow(PolicyCondition):
    """Matches if gate score is below threshold."""

    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def matches(self, ctx: PhaseContext) -> bool:
        return ctx.gate_score < self.threshold

    def __repr__(self) -> str:
        return f"GateBelow({self.threshold})"


class RetryExhausted(PolicyCondition):
    """Matches if recovery retries are exhausted for a scenario."""

    def __init__(self, scenario: str | None = None) -> None:
        self.scenario = scenario

    def matches(self, ctx: PhaseContext) -> bool:
        if self.scenario:
            return self.scenario in ctx.retry_exhausted_scenarios
        return len(ctx.retry_exhausted_scenarios) > 0

    def __repr__(self) -> str:
        return f"RetryExhausted(scenario={self.scenario!r})"


class TimedOut(PolicyCondition):
    """Matches if elapsed time exceeds duration."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def matches(self, ctx: PhaseContext) -> bool:
        return ctx.elapsed_seconds > self.seconds

    def __repr__(self) -> str:
        return f"TimedOut({self.seconds}s)"


class AllAgentsComplete(PolicyCondition):
    def matches(self, ctx: PhaseContext) -> bool:
        return ctx.completed_roles | ctx.failed_roles == ctx.all_roles

    def __repr__(self) -> str:
        return "AllAgentsComplete()"


class CostExceeded(PolicyCondition):
    """Matches if total cost exceeds budget."""

    def __init__(self, budget_usd: float) -> None:
        self.budget_usd = budget_usd

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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.action.value}
        if self.params:
            d["params"] = self.params
        return d


@dataclass(frozen=True)
class PolicyRule:
    """A named rule with condition, action(s), and priority."""

    name: str
    condition: PolicyCondition
    actions: list[PolicyActionData]
    priority: int = 50  # lower = higher priority


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


# -- Engine --


class PolicyEngine:
    """Evaluates rules against a PhaseContext."""

    def __init__(self, rules: list[PolicyRule]) -> None:
        self._rules = sorted(rules, key=lambda r: r.priority)

    def evaluate(self, ctx: PhaseContext) -> list[PolicyActionData]:
        """Evaluate all rules. Returns the flat list of matching actions."""
        actions: list[PolicyActionData] = []
        for rule in self._rules:
            if rule.condition.matches(ctx):
                actions.extend(rule.actions)
        return actions

    @property
    def rules(self) -> list[PolicyRule]:
        return list(self._rules)
