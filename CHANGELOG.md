# Changelog

All notable changes to `techrevati-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the
caveat that 0.x APIs are explicitly unstable.

## [0.1.0.dev1] — 2026-05-20

Sprint 2 milestone toward the 0.1.0 async-first release. APIs added here
are unstable; the contract becomes stable when 0.1.0 ships.

### Added
- `AsyncCircuitBreaker` — `asyncio.Lock`-based sibling of `CircuitBreaker`
  with the same `half_open_max_probes` serialization semantics and
  injectable monotonic `clock`. Independent state from the sync variant.
- `AsyncOrchestrationSession` + `Orchestrator.asession()` async context
  manager. Mirrors the sync session API; `arun_turn` and `arun_tool`
  drive async coro factories. Sync helpers (`authorize`, `evaluate_policy`,
  `evaluate_gate`, `summary`, lifecycle methods) are inherited from a
  shared `_SessionBase` so behavior stays in lock-step.
- `arun_turn(timeout=...)` enforces the deadline with `asyncio.wait_for`.
  `run_turn(timeout=...)` enforces it via a one-shot
  `ThreadPoolExecutor`. Both raise `TurnTimeoutError`.
- `TurnTimeoutError` — single error class spanning both code paths so
  callers don't need to catch `concurrent.futures.TimeoutError` and
  `asyncio.TimeoutError` separately.
- `AgentStatus.CANCELLED` terminal state. `asyncio.CancelledError`
  bubbling out of an `async with orch.asession()` body transitions the
  worker to CANCELLED instead of FAILED, then re-raises.
- `aattempt_recovery(scenario, ctx, *, sleeper=asyncio.sleep)` — async
  sibling of `attempt_recovery`. Accepts an injectable sleeper today
  (no recipe currently sleeps, but the contract is fixed for future
  steps).
- `AsyncOrchestrationSession.pause_for_input(prompt)` — async
  human-in-the-loop hook. Transitions worker to `WAITING_FOR_INPUT`,
  returns an awaitable that the caller resolves via
  `session.provide_input(value)`.
- `AgentRegistry` and `_SessionBase` now record session start time on
  construction; `evaluate_policy()` auto-computes `elapsed_seconds` when
  the caller does not provide one. Closes the `TimedOut`-never-fires
  gap from 0.0.x.
- `RUNNING → WAITING_FOR_INPUT` transition is now valid (was missing).
- `pytest-asyncio==1.3.0` added to the `[dev]` extras.

### Changed
- `evaluate_policy(elapsed_seconds=...)` parameter is now `float | None`
  (default `None`). Behavior change: when omitted, time-based policies
  finally see real elapsed seconds. Explicit `elapsed_seconds=0.0`
  callers will need to migrate; pass the value you want.

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

[0.1.0.dev1]: https://github.com/Techrevati/runtime/releases/tag/v0.1.0.dev1
[0.0.1]: https://github.com/Techrevati/runtime/releases/tag/v0.0.1
[0.0.0]: https://github.com/Techrevati/runtime/releases/tag/v0.0.0
