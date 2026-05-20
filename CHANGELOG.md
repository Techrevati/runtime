# Changelog

All notable changes to `techrevati-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the
caveat that 0.x APIs are explicitly unstable.

## [Unreleased] — Sprint 6 (testing rigor)

Hardening work between 0.1.0 and 0.2.0. Public API is unchanged; this is
test-suite and CI-only.

### Added
- **Hypothesis** as a dev dependency. Two new property test modules:
  `tests/test_property_retry_policy.py` exercises `classify_exception` over
  arbitrary strings and verifies `backoff_delay` invariants across all four
  jitter modes; `tests/test_property_circuit_breaker.py` drives the
  `CircuitBreaker` state machine through random op sequences with the
  injectable `ManualClock` and asserts invariants the example-based tests
  could not enumerate.
- **pytest-randomly** as a dev dependency. The default test runner now
  shuffles test order on every invocation, surfacing hidden order
  dependencies. Suite passes deterministically under shuffled ordering.
- **`ManualClock`** test double promoted from per-module duplication in
  `test_circuit_breaker.py` / `test_async_circuit_breaker.py` to
  `tests/conftest.py` as both an importable class and a `manual_clock`
  fixture. Sprint 8 rate-limiter / scheduler primitives that accept an
  injectable monotonic clock will plug into it without re-inventing the
  type.
- **`scripts/check_module_coverage.py`** — per-module coverage floor
  checker. Wired into `.github/workflows/ci.yml` to fail builds when any
  module in `src/techrevati/` drops below 85% statement coverage. The
  global `--cov-fail-under=90` did not catch `permissions.py` slipping to
  82% in 0.1.0; this closes that gap.

### Changed
- `tests/test_permissions.py` now covers `PermissionOutcome.to_dict()` for
  both the minimal and fully-populated cases. `permissions.py` moves from
  82% → 100% statement coverage; aggregate suite coverage 93.79% → 94.91%.

## [0.1.0] — 2026-05-20

First beta release. Closes the primitive-parity gap with 2026 agent
SDKs and ships the async path the 0.0.x docstring had been falsely
advertising. APIs in this release are intended to be stable; breaking
changes between 0.1.x and 0.2.0 will be documented in
[`docs/migrating-from-0.0.x.md`](https://github.com/Techrevati/runtime/blob/main/docs/migrating-from-0.0.x.md) and
gated by deprecation warnings.

Consolidates the work of pre-release tags `0.1.0.dev1`, `0.1.0.dev2`,
`0.1.0.dev3`, and `0.1.0.rc1`. Per-sprint detail is in the git log.

### Added — Async path
- `AsyncCircuitBreaker` mirrors `CircuitBreaker` semantics with
  `asyncio.Lock`, same `half_open_max_probes` serialization,
  injectable monotonic `clock`. State independent of the sync
  variant.
- `Orchestrator.asession()` returns an `AsyncOrchestrationSession`.
  `arun_turn` and `arun_tool` drive async coro factories; sync
  helpers (`authorize`, `evaluate_policy`, `evaluate_gate`,
  `summary`, lifecycle methods) shared with the sync session via
  `_SessionBase`.
- `aattempt_recovery(scenario, ctx, *, sleeper=asyncio.sleep)` async
  sibling of `attempt_recovery` with injectable sleeper contract.
- `arun_turn(timeout=...)` enforces deadlines with
  `asyncio.wait_for`; sync `run_turn(timeout=...)` uses a one-shot
  `ThreadPoolExecutor`. Both raise `TurnTimeoutError` for a single
  catchable error class across code paths.
- `AgentStatus.CANCELLED` terminal state. `asyncio.CancelledError`
  out of `async with orch.asession()` transitions the worker to
  CANCELLED and re-raises.
- `AsyncOrchestrationSession.pause_for_input(prompt)` async
  human-in-the-loop hook. Transitions worker to `WAITING_FOR_INPUT`;
  resolve from elsewhere via `session.provide_input(value)`.
- `RUNNING → WAITING_FOR_INPUT` is now a valid transition (was
  missing in 0.0.x).

### Added — Industry primitive parity
- `MaxIterationsExceededError` + `Orchestrator(max_iterations=25)`
  cap. Default matches OpenAI Agents SDK; counted across both
  `run_turn` and `arun_turn`. Anthropic explicitly names stopping
  conditions as a production-readiness requirement.
- `Handoff` immutable dataclass (`techrevati.runtime.handoffs`) +
  `OrchestrationSession.handoff_to(target_role, reason, context)`.
  Finalizes the source worker as COMPLETED, registers a fresh worker
  for the target role under the same project_id, returns a Handoff
  describing the delegation. Enables Anthropic's orchestrator-workers
  pattern on top of our primitives.
- `Guardrail` Protocol + `GuardrailOutcome` + `GuardrailViolatedError`
  (`techrevati.runtime.guardrails`). `Orchestrator(guardrails=[...])`
  auto-runs `check_pre` before and `check_post` after every
  `run_tool` / `arun_tool` invocation; first violation raises with
  guardrail name, stage, role, tool. Mirrors the OpenAI Agents SDK
  guardrail model.
- `AllowAllGuardrail` reference no-op implementation.
- `AgentSession` alias for `Orchestrator`. The 0.2.0 rename will
  promote `AgentSession` to the canonical name with `Orchestrator`
  kept as a deprecation alias; adopting the new name now is
  forward-compatible.

### Added — Observability
- `EventSink` and `UsageSink` Protocols (`techrevati.runtime.sinks`),
  plus `NoopEventSink`, `NoopUsageSink`, `RingBufferEventSink`,
  `RingBufferUsageSink` defaults. `RingBufferEventSink` enforces a
  configurable capacity (default 1000) so long-running sessions
  can't balloon memory — closes the unbounded-tracker gap from
  0.0.x.
- `Orchestrator(event_sink=..., usage_sink=...)` plumbs the
  configured sinks through every session. Every `AgentEvent` the
  session records is forwarded to the event sink; every recorded
  turn is forwarded to the usage sink with its computed cost. A
  misbehaving sink cannot tear down the session — emit failures
  log and are swallowed.
- `OpenTelemetrySink` and `OpenTelemetryUsageSink`
  (`techrevati.runtime.otel`, available via the new `[otel]`
  extra). Mirrors every event as a one-shot OTel span with
  `gen_ai.operation.name`, `gen_ai.provider.name`,
  `gen_ai.agent.name`, optional `gen_ai.agent.id`, and `error.type`
  on failures. Records `gen_ai.client.token.usage` histogram (with
  `gen_ai.token.type=input|output` discriminator) and a
  `techrevati.cost.usd` counter. Span names follow the GenAI agent
  spans convention (`create_agent` / `invoke_agent` /
  `execute_tool` / `invoke_workflow`).
- `[otel]` extra: `opentelemetry-api>=1.27`,
  `opentelemetry-sdk>=1.27`,
  `opentelemetry-semantic-conventions>=0.48b0`.
- Structured `logger.info` calls at five decision points: recovery
  attempted, session failed, quality gate failed, handoff issued,
  budget exceeded. All with `extra={role, phase, project_id, ...}`
  so log shippers can pivot by role.

### Added — Docs and DX
- `docs/tutorials/end-to-end.md` walks every primitive composed
  together with sync, async, and OTel switchover.
- `examples/tiny_agent.py` runnable companion (not bundled in
  wheel). Smoke-tested end-to-end.
- `examples/pricing.json` reference template with illustrative
  Claude / GPT pricing (not normative).
- `docs/api/*.md` eight `mkdocstrings`-backed API reference pages
  via the Python handler.
- `docs/patterns/orchestrator.md` rewritten with
  When-to-use / Anti-patterns / Tuning template + a prominent
  naming-disambiguation callout separating our `Orchestrator` from
  Anthropic's *orchestrator-workers* delegation pattern.
- `CONTRIBUTING.md`, `SECURITY.md`, `CODEOWNERS`,
  `.github/dependabot.yml`, `.github/ISSUE_TEMPLATE/{bug,feature}.md`.

### Changed
- `evaluate_policy(elapsed_seconds=...)` is now `float | None`
  (default `None`). When omitted, elapsed is auto-computed from
  session start so `TimedOut` conditions finally fire. Callers
  passing explicit `0.0` previously must migrate to pass the value
  they actually want.
- `AgentRegistry` and `_SessionBase` record session start time on
  construction.
- README revised end-to-end. Headline pitch matches what the
  package now does (sync **and** async, four standard primitives,
  OTel GenAI semconv). New *"Why not LangGraph / OpenAI Agents
  SDK?"* positioning section. Classifier bumped from `3 - Alpha`
  to `4 - Beta`.
- README tagline reflects beta status.

### Notes
- Tool input gating is pre-call site (role + tool name) gating +
  post-call value gating. True input-value gating arrives when we
  have a typed tool input model (post-0.2.0).
- Guardrail violations are not retried automatically — they raise.
- Span nesting (parent/child relationships across agent/turn/tool)
  is not yet emitted — discrete spans + `gen_ai.agent.id` give
  correlation. Nesting is targeted for 0.2.0.
- The `[dev]` extra now installs OpenTelemetry SDK packages so the
  optional `otel` module type-checks and tests run under
  `mypy --strict`.

## [0.0.1] — 2026-05-20

### Fixed
- `mypy --strict` now passes. Added `src/techrevati/__init__.py` namespace
  marker (PEP 420) so the wheel layout no longer double-maps modules. Removed
  `continue-on-error: true` from the CI mypy step, which had been silently
  swallowing failures since 0.0.0.
- `CircuitBreaker` uses `time.monotonic` instead of `time.time` for duration
  checks. NTP/clock jumps no longer stick the breaker open or close it early.
- Removed inaccurate `"Production async runtime"` claim from the package
  docstring. Async support is targeted for 0.1.0; this version is sync only.

### Added
- `BudgetExceededError` plus `Orchestrator(enforce_budget=True)` flag. When
  enabled, `run_turn` raises after the cumulative cost exceeds `budget_usd`.
  The default remains backwards-compatible (records an event, returns
  normally) so existing callers see no behavior change unless they opt in.
- `has_pricing(model)` helper. `UsageTracker.record_turn` now emits a one-time
  `WARNING` per process per model when pricing has not been registered. This
  closes the silent-$0 footgun where unregistered models produced no cost
  signal.
- `CircuitBreaker.half_open_max_probes` (default `1`). Concurrent half-open
  probes are now serialized; previously the lock was released before `fn()`
  ran, letting unbounded threads stampede a recovering service. Probe in-flight
  counting is tracked under the same lock as state transitions. Conforms to
  the Polly default; raise to N for Resilience4j-style behavior.
- `CircuitBreaker.clock` parameter accepting a `Callable[[], float]`. Defaults
  to `time.monotonic`. Test code injects a manual clock to make recovery-window
  tests deterministic; `time.sleep` is no longer used in the test suite.
- `backoff_delay(jitter=...)` accepts `"none"`, `"full"`, `"equal"`, or
  `"decorrelated"` mode strings. Bool values are still accepted for backward
  compatibility (`True` maps to `"full"`, `False` to `"none"`). New `cap` and
  `prev_delay` parameters support standard AWS Architecture Blog formulas
  (Marc Brooker, exponential backoff & jitter).
- README "Limitations" section documenting sync-only constraint, in-memory
  tracker growth, pricing-not-bundled default, advisory permissions, lack of
  durable execution, and lack of OTel integration.

### Changed
- **Default jitter algorithm is now decorrelated** (was full-additive 25%
  jitter). Per AWS Builders' Library, decorrelated is the fastest of the four
  documented algorithms. The change affects code calling `backoff_delay()`
  with default jitter; pass `jitter="equal"` for behavior closest to the
  previous default.
- README tagline reflects alpha status until 0.2.0 (was "Production runtime
  primitives ...").
- `[project.optional-dependencies] dev` now pins `pytest`, `pytest-cov`,
  `mypy`, and `ruff` to exact versions matching `.pre-commit-config.yaml`.
  Local lint and CI lint can no longer disagree.
- CI now installs the package with `pip install -e ".[dev]"` instead of
  unpinned `pip install pytest pytest-cov ruff mypy`. `actions/setup-python`
  bumped from v4 to v5 with pip caching. `codecov/codecov-action` bumped from
  v3 to v4. The `PYTHONPATH=src` workaround in pytest is gone (now resolved
  by the namespace marker).

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

[0.1.0]: https://github.com/Techrevati/runtime/releases/tag/v0.1.0
[0.0.1]: https://github.com/Techrevati/runtime/releases/tag/v0.0.1
[0.0.0]: https://github.com/Techrevati/runtime/releases/tag/v0.0.0
