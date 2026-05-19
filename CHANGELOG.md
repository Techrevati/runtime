# Changelog

All notable changes to `techrevati-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the
caveat that 0.x APIs are explicitly unstable.

## [0.0.0] — 2026-05-20

Initial public release under the `techrevati-runtime` namespace.

Provides the foundational primitives for orchestrating multi-step LLM agent
loops with reliability and cost visibility:

- `Orchestrator` + `OrchestrationSession` — single-loop wiring of lifecycle,
  usage, retry classification, circuit breaker, permissions, and policy.
- `CircuitBreaker` — three-state fault-tolerant execution wrapper.
- `RecoveryContext` + `attempt_recovery` + `classify_exception` — failure
  classification and recipe lookup.
- `UsageTracker` + `register_pricing` + `load_pricing_from_file` — per-model
  cost tracking with caller-provided pricing (no bundled pricing data).
- `QualityGate` + `QualityLevel` — graduated pass/fail evaluation.
- `AgentRegistry` + `AgentWorker` — validated lifecycle state machine.
- `AgentEvent` — typed lifecycle events with an OpenTelemetry attribute bridge.
- `PermissionPolicy` + `PermissionEnforcer` — deny-first role × tool gating.
- `PolicyEngine` + composable conditions — declarative rule evaluator.

[0.0.0]: https://github.com/Techrevati/runtime/releases/tag/v0.0.0
