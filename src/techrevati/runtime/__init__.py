"""
techrevati.runtime — Production async runtime primitives for LLM agent loops.

Reliability, cost tracking, and lifecycle for multi-step agent execution.
Zero runtime dependencies.

>>> from techrevati.runtime import Orchestrator, UsageSnapshot
>>> from techrevati.runtime import classify_exception, attempt_recovery, RecoveryContext
>>> from techrevati.runtime import CircuitBreaker, PolicyEngine
"""

__version__ = "0.0.0"

from techrevati.runtime.quality_gate import (
    QualityLevel,
    QualityGate,
    QualityGateOutcome,
)
from techrevati.runtime.agent_events import (
    AgentEventName,
    AgentEventStatus,
    AgentFailureClass,
    AgentEvent,
)
from techrevati.runtime.permissions import (
    PermissionMode,
    RolePermissionConfig,
    PermissionOutcome,
    PermissionPolicy,
    PermissionEnforcer,
)
from techrevati.runtime.usage_tracking import (
    ModelPricing,
    UsageSnapshot,
    UsageTracker,
    PRICING_TABLE,
    register_pricing,
    load_pricing_from_file,
)
from techrevati.runtime.agent_lifecycle import (
    AgentStatus,
    AgentWorkerEvent,
    AgentWorker,
    AgentRegistry,
    InvalidTransitionError,
)
from techrevati.runtime.retry_policy import (
    FailureScenario,
    RecoveryStep,
    EscalationPolicy,
    RecoveryRecipe,
    RecoveryContext,
    RecoveryResult,
    RecoveryEvent,
    recipe_for,
    attempt_recovery,
    classify_exception,
    backoff_delay,
    next_provider,
    smaller_context_budget,
)
from techrevati.runtime.policy_engine import (
    PolicyCondition,
    PolicyAction,
    PolicyActionData,
    PolicyRule,
    PhaseContext,
    PolicyEngine,
)
from techrevati.runtime.circuit_breaker import (
    CircuitState,
    CircuitBreaker,
    CircuitOpenError,
)
from techrevati.runtime.orchestrator import (
    Orchestrator,
    OrchestrationSession,
    PermissionDeniedError,
)
