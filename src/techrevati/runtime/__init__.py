"""
techrevati.runtime — Runtime primitives for multi-step LLM agent loops.

Reliability, cost tracking, and lifecycle for multi-step agent execution.
Zero runtime dependencies.

>>> from techrevati.runtime import AgentSession, UsageSnapshot
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
    StepCheckpointSaver,
    StepRecord,
)
from techrevati.runtime.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)
from techrevati.runtime.governance import (
    BreachAction,
    GovernanceBreachError,
    GovernancePlane,
    GovernanceState,
    Limit,
    LimitOutcome,
    LimitScope,
    MaxBudgetLimit,
    MaxConsecutiveFailuresLimit,
    MaxIterationsLimit,
    MaxToolCallsLimit,
)
from techrevati.runtime.guardrails import (
    AllowAllGuardrail,
    AsyncGuardrail,
    Guardrail,
    GuardrailOutcome,
    GuardrailStage,
    GuardrailViolatedError,
    GuardrailViolation,
    PatternGuardrail,
    PromptInjectionGuardrail,
)
from techrevati.runtime.handoffs import Handoff
from techrevati.runtime.hooks import (
    AsyncHook,
    Hook,
    HookBudgetExceededError,
    HookContext,
    LogModelIOHook,
    RedactPIIHook,
    TokenBudgetCheckHook,
)
from techrevati.runtime.memory import (
    CompactionStrategy,
    ConversationMemory,
    InMemoryConversationMemory,
    MemoryMessage,
    NoCompaction,
    TokenBudgetCompaction,
    WindowCompaction,
)
from techrevati.runtime.orchestrator import (
    AgentSession,
    AsyncOrchestrationSession,
    MaxIterationsExceededError,
    OrchestrationSession,
    Orchestrator,
    PermissionDeniedError,
    TurnTimeoutError,
)
from techrevati.runtime.output_spec import (
    CallableOutputSpec,
    JsonOutputSpec,
    OutputSpec,
    OutputValidationError,
    RegexOutputSpec,
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
from techrevati.runtime.streaming import (
    StreamEvent,
    StreamEventType,
    StreamFinalStatus,
)
from techrevati.runtime.usage_tracking import (
    PRICING_TABLE,
    BudgetExceededError,
    ModelPricing,
    PricingAlreadyRegisteredError,
    UsageBoundExceededError,
    UsageLimitExceededError,
    UsageLimits,
    UsageSnapshot,
    UsageTracker,
    has_pricing,
    load_pricing_from_file,
    register_pricing,
    resolve_pricing,
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
    "AsyncGuardrail",
    "AsyncHook",
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
    "BreachAction",
    "GovernanceBreachError",
    "GovernancePlane",
    "GovernanceState",
    "GuardrailStage",
    "GuardrailViolatedError",
    "GuardrailViolation",
    "Handoff",
    "Hook",
    "HookBudgetExceededError",
    "HookContext",
    "InMemorySaver",
    "InvalidTransitionError",
    "Limit",
    "LimitOutcome",
    "LimitScope",
    "LogModelIOHook",
    "ManualClock",
    "MaxBudgetLimit",
    "MaxConsecutiveFailuresLimit",
    "MaxIterationsExceededError",
    "MaxIterationsLimit",
    "MaxToolCallsLimit",
    "ModelPricing",
    "NoopEventSink",
    "NoopUsageSink",
    "OrchestrationSession",
    "PatternGuardrail",
    "PromptInjectionGuardrail",
    "Orchestrator",
    "RedactPIIHook",
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
    "PricingAlreadyRegisteredError",
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
    "StepCheckpointSaver",
    "StepRecord",
    "StaticProviderRouter",
    "CompactionStrategy",
    "ConversationMemory",
    "InMemoryConversationMemory",
    "MemoryMessage",
    "NoCompaction",
    "TokenBudgetCompaction",
    "WindowCompaction",
    "CallableOutputSpec",
    "JsonOutputSpec",
    "OutputSpec",
    "OutputValidationError",
    "RegexOutputSpec",
    "StreamEvent",
    "StreamEventType",
    "StreamFinalStatus",
    "SystemClock",
    "TokenBucket",
    "TokenBudgetCheckHook",
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
    "resolve_pricing",
    "smaller_context_budget",
]
