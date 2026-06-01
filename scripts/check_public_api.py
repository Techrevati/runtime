"""Ensure package-level public exports match imported runtime symbols."""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from pathlib import Path

ALLOWED_EXPLICIT_EXPORTS = {"__version__"}
EXPECTED_RUNTIME_EXPORTS = (
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
    "StaticProviderRouter",
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
    "smaller_context_budget",
)


def _runtime_init(root: Path) -> Path:
    return root / "src" / "techrevati" / "runtime" / "__init__.py"


def _exported_names(tree: ast.Module) -> tuple[list[str], list[str]]:
    exports: list[str] | None = None
    failures: list[str] = []

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.List):
            failures.append("__all__ must be a literal list")
            continue
        values: list[str] = []
        for item in node.value.elts:
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                failures.append("__all__ entries must be literal strings")
                continue
            values.append(item.value)
        exports = values

    if exports is None:
        failures.append("__all__ is missing")
        exports = []
    return exports, failures


def _imported_runtime_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if not module.startswith("techrevati.runtime."):
            continue
        for alias in node.names:
            name = alias.asname or alias.name
            if not name.startswith("_"):
                names.add(name)
    return names


def _check_public_api(
    text: str,
    *,
    expected_exports: Sequence[str] = EXPECTED_RUNTIME_EXPORTS,
) -> list[str]:
    tree = ast.parse(text)
    exports, failures = _exported_names(tree)
    exported = set(exports)
    imported = _imported_runtime_names(tree)
    expected = list(expected_exports)
    expected_set = set(expected)

    if len(exports) != len(exported):
        seen: set[str] = set()
        duplicates = sorted(
            name for name in exports if name in seen or seen.add(name) is not None
        )
        failures.append(f"__all__ contains duplicate exports: {duplicates!r}")

    if len(expected) != len(expected_set):
        seen = set()
        duplicates = sorted(
            name for name in expected if name in seen or seen.add(name) is not None
        )
        failures.append(f"frozen public API contains duplicate exports: {duplicates!r}")

    for name in sorted(imported - exported):
        failures.append(f"runtime import is missing from __all__: {name}")

    allowed_extra = imported | ALLOWED_EXPLICIT_EXPORTS
    for name in sorted(exported - allowed_extra):
        failures.append(f"__all__ exports unknown name: {name}")

    for name in sorted(exported):
        if name.startswith("_") and name not in ALLOWED_EXPLICIT_EXPORTS:
            failures.append(f"__all__ exports private name: {name}")

    missing_frozen = sorted(expected_set - exported)
    if missing_frozen:
        failures.append(f"public API is missing frozen exports: {missing_frozen!r}")

    extra_frozen = sorted(exported - expected_set)
    if extra_frozen:
        failures.append(
            f"public API exports names outside frozen set: {extra_frozen!r}"
        )

    if not missing_frozen and not extra_frozen and exports != expected:
        failures.append("public API export order changed; update the frozen list")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing src/techrevati/runtime.",
    )
    args = parser.parse_args()

    init_file = _runtime_init(args.root)
    failures = _check_public_api(init_file.read_text(encoding="utf-8"))
    if failures:
        print("public API check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Public API check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
