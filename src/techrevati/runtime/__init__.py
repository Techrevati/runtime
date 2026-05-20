"""
techrevati.runtime — Runtime primitives for multi-step LLM agent loops.

Reliability, cost tracking, and lifecycle for multi-step agent execution.
Zero runtime dependencies.

>>> from techrevati.runtime import Orchestrator, UsageSnapshot
>>> from techrevati.runtime import classify_exception, attempt_recovery, RecoveryContext
>>> from techrevati.runtime import CircuitBreaker, PolicyEngine
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("techrevati-runtime")
except PackageNotFoundError:
    # Editable / source checkout without an installed dist — fall back to
    # the in-tree version so imports still work during local development.
    __version__ = "0.0.0+local"

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
from techrevati.runtime.checkpoint import (
    Checkpoint,
    CheckpointSaver,
    InMemorySaver,
    SqliteSaver,
)
from techrevati.runtime.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)
from techrevati.runtime.guardrails import (
    AllowAllGuardrail,
    Guardrail,
    GuardrailOutcome,
    GuardrailStage,
    GuardrailViolatedError,
)
from techrevati.runtime.handoffs import Handoff
from techrevati.runtime.orchestrator import (
    AgentSession,
    AsyncOrchestrationSession,
    MaxIterationsExceededError,
    OrchestrationSession,
    Orchestrator,
    PermissionDeniedError,
    TurnTimeoutError,
)
from techrevati.runtime.permissions import (
    PermissionEnforcer,
    PermissionMode,
    PermissionOutcome,
    PermissionPolicy,
    RolePermissionConfig,
)
from techrevati.runtime.persistence import (
    SqliteEventSink,
    SqliteUsageSink,
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
from techrevati.runtime.rate_limit import (
    AsyncRateLimiter,
    AsyncTokenBucket,
    RateLimiter,
    RateLimitExceededError,
    TokenBucket,
)
from techrevati.runtime.retry_policy import (
    EscalationPolicy,
    FailureScenario,
    RecoveryContext,
    RecoveryEvent,
    RecoveryRecipe,
    RecoveryResult,
    RecoveryStep,
    aattempt_recovery,
    attempt_recovery,
    backoff_delay,
    classify_exception,
    next_provider,
    recipe_for,
    smaller_context_budget,
)
from techrevati.runtime.routing import (
    ProviderRouter,
    RoundRobinProviderRouter,
    StaticProviderRouter,
    WeightedProviderRouter,
)
from techrevati.runtime.scheduler import (
    Clock,
    ManualClock,
    SystemClock,
)
from techrevati.runtime.sinks import (
    DEFAULT_RING_CAPACITY,
    EventSink,
    NoopEventSink,
    NoopUsageSink,
    RingBufferEventSink,
    RingBufferUsageSink,
    UsageSink,
)
from techrevati.runtime.usage_tracking import (
    PRICING_TABLE,
    BudgetExceededError,
    ModelPricing,
    UsageBoundExceededError,
    UsageLimitExceededError,
    UsageLimits,
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
    "AgentSession",
    "AgentStatus",
    "AgentWorker",
    "AgentWorkerEvent",
    "AllowAllGuardrail",
    "AsyncCircuitBreaker",
    "AsyncOrchestrationSession",
    "AsyncRateLimiter",
    "AsyncTokenBucket",
    "BudgetExceededError",
    "Checkpoint",
    "CheckpointSaver",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "Clock",
    "DEFAULT_RING_CAPACITY",
    "EscalationPolicy",
    "EventSink",
    "FailureScenario",
    "Guardrail",
    "GuardrailOutcome",
    "GuardrailStage",
    "GuardrailViolatedError",
    "Handoff",
    "InMemorySaver",
    "InvalidTransitionError",
    "ManualClock",
    "MaxIterationsExceededError",
    "ModelPricing",
    "NoopEventSink",
    "NoopUsageSink",
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
    "ProviderRouter",
    "QualityGate",
    "QualityGateOutcome",
    "QualityLevel",
    "RateLimitExceededError",
    "RateLimiter",
    "RecoveryContext",
    "RecoveryEvent",
    "RecoveryRecipe",
    "RecoveryResult",
    "RecoveryStep",
    "RingBufferEventSink",
    "RingBufferUsageSink",
    "RolePermissionConfig",
    "RoundRobinProviderRouter",
    "SqliteEventSink",
    "SqliteSaver",
    "SqliteUsageSink",
    "StaticProviderRouter",
    "SystemClock",
    "TokenBucket",
    "TurnTimeoutError",
    "UsageBoundExceededError",
    "UsageLimitExceededError",
    "UsageLimits",
    "UsageSink",
    "UsageSnapshot",
    "UsageTracker",
    "WeightedProviderRouter",
    "__version__",
    "aattempt_recovery",
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
