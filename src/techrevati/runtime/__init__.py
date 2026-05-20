"""
techrevati.runtime — Runtime primitives for multi-step LLM agent loops.

Reliability, cost tracking, and lifecycle for multi-step agent execution.
Zero runtime dependencies.

>>> from techrevati.runtime import Orchestrator, UsageSnapshot
>>> from techrevati.runtime import classify_exception, attempt_recovery, RecoveryContext
>>> from techrevati.runtime import CircuitBreaker, PolicyEngine
"""

__version__ = "0.0.1"

from techrevati.runtime.agent_events import (
    AgentEvent,
    AgentEventName,
    AgentEventStatus,
    AgentFailureClass,
)
from techrevati.runtime.agent_lifecycle import (
    AgentRegistry,
    AgentStatus,
    AgentWorker,
    AgentWorkerEvent,
    InvalidTransitionError,
)
from techrevati.runtime.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)
from techrevati.runtime.orchestrator import (
    OrchestrationSession,
    Orchestrator,
    PermissionDeniedError,
)
from techrevati.runtime.permissions import (
    PermissionEnforcer,
    PermissionMode,
    PermissionOutcome,
    PermissionPolicy,
    RolePermissionConfig,
)
from techrevati.runtime.policy_engine import (
    PhaseContext,
    PolicyAction,
    PolicyActionData,
    PolicyCondition,
    PolicyEngine,
    PolicyRule,
)
from techrevati.runtime.quality_gate import (
    QualityGate,
    QualityGateOutcome,
    QualityLevel,
)
from techrevati.runtime.retry_policy import (
    EscalationPolicy,
    FailureScenario,
    RecoveryContext,
    RecoveryEvent,
    RecoveryRecipe,
    RecoveryResult,
    RecoveryStep,
    attempt_recovery,
    backoff_delay,
    classify_exception,
    next_provider,
    recipe_for,
    smaller_context_budget,
)
from techrevati.runtime.usage_tracking import (
    PRICING_TABLE,
    BudgetExceededError,
    ModelPricing,
    UsageSnapshot,
    UsageTracker,
    has_pricing,
    load_pricing_from_file,
    register_pricing,
)

__all__ = [
    "AgentEvent",
    "AgentEventName",
    "AgentEventStatus",
    "AgentFailureClass",
    "AgentRegistry",
    "AgentStatus",
    "AgentWorker",
    "AgentWorkerEvent",
    "BudgetExceededError",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "EscalationPolicy",
    "FailureScenario",
    "InvalidTransitionError",
    "ModelPricing",
    "OrchestrationSession",
    "Orchestrator",
    "PermissionDeniedError",
    "PermissionEnforcer",
    "PermissionMode",
    "PermissionOutcome",
    "PermissionPolicy",
    "PhaseContext",
    "PolicyAction",
    "PolicyActionData",
    "PolicyCondition",
    "PolicyEngine",
    "PolicyRule",
    "PRICING_TABLE",
    "QualityGate",
    "QualityGateOutcome",
    "QualityLevel",
    "RecoveryContext",
    "RecoveryEvent",
    "RecoveryRecipe",
    "RecoveryResult",
    "RecoveryStep",
    "RolePermissionConfig",
    "UsageSnapshot",
    "UsageTracker",
    "__version__",
    "attempt_recovery",
    "backoff_delay",
    "classify_exception",
    "has_pricing",
    "load_pricing_from_file",
    "next_provider",
    "recipe_for",
    "register_pricing",
    "smaller_context_budget",
]
