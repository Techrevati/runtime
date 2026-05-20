# Changelog

All notable changes to `techrevati-runtime` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html), with the
caveat that 0.x APIs are explicitly unstable.

## [0.2.1] — 2026-05-20

Sharp-edges patch landed the same day as 0.2.0 to close silent footguns
identified in the 0.3.0 migration audit. No new primitives; one
intentional soft-breaking semantic change to `GuardrailViolatedError`
(callers reading the legacy single-violation fields still work — see
"Changed" below).

### Fixed

- **`RecoveryRecipe.step_retries` is now honored** by `attempt_recovery`
  and `aattempt_recovery`. Previously the field existed on the dataclass
  but the recovery executors did not consume it — a recipe that set
  `step_retries={RecoveryStep.RETRY_WITH_BACKOFF: 3}` silently ran the
  step exactly once. Now the executor retries the step up to the
  budgeted count before moving to the next step. Missing keys default
  to a single attempt, preserving 0.2.0 behavior.
- **`OpenTelemetrySink` cleans up orphan parent spans on interpreter
  exit.** If a process died between `AGENT_STARTED` / `PHASE_STARTED`
  and the matching `AGENT_COMPLETED` / `AGENT_FAILED` / `PHASE_COMPLETED`,
  the parent span previously stayed open in the exporter buffer and
  corrupted the APM trace tree. An `atexit` hook now marks every
  still-open parent with `error.type=abrupt_termination` and an `ERROR`
  status before ending it. The hook is no-op on the clean-exit path.

### Added

- **`register_pricing(model, pricing, *, on_conflict="overwrite")`** — explicit
  merge semantics. `"overwrite"` (default) preserves 0.2.0 behavior;
  `"error"` raises `PricingAlreadyRegisteredError` on re-registration;
  `"keep"` retains the existing entry and drops the new pricing silently.
  Useful for "register defaults if not present" startup patterns.
- **`PricingAlreadyRegisteredError`** — exported from
  `techrevati.runtime`. Subclass of `ValueError`, carries `.model`.
- **`GuardrailViolation`** dataclass — one entry in the new
  `GuardrailViolatedError.violations` tuple. Carries `outcome`,
  `guardrail` (name), `stage` (`"pre"` / `"post"`). Has `to_dict()` for
  audit-log serialization.
- **`DeprecationWarning` on `Orchestrator(...)` instantiation** — emitted
  once per process. `AgentSession` has been the canonical class name
  since 0.2.0; the alias remains for a deprecation window and will be
  removed in 0.3.0. Silent in import — only the first construction
  warns.

### Changed

- **`GuardrailViolatedError.violations`** — every guardrail that fires
  at the same stage is now collected and surfaced as a tuple on the
  raised error, instead of short-circuiting on the first violation.
  Required for EU AI Act Article 12 record-keeping (audit logs must
  reflect the full set of guardrails that fired). Legacy callers that
  read `error.outcome` / `error.guardrail` / `error.stage` still work —
  those attributes mirror the first violation. The orchestrator now
  runs every pre-check and post-check before raising; tests that
  asserted short-circuit behavior have been updated.

## [0.2.0] — 2026-05-20

Durable execution, token-aware rate limiting, OTel agent-level span
nesting, granular usage limits, supply-chain hardening. Zero new
runtime dependencies. Two soft-breaking changes: OTel sink wire format
(one-shot → nested) and `UsageLimitExceededError` for non-cost
overruns (see [Migrating from 0.1.x](migrating-from-0.1.x.md)).

### Added — Sprint 6 (supply chain + release polish)

- **CycloneDX SBOM in `release.yml`** — generated via `cyclonedx-bom`
  (dev-only) before publish and attached to the GitHub Release as
  both JSON and XML. PyPI Trusted Publishing already attaches a
  Sigstore-backed attestation to each artifact; `SECURITY.md` now
  documents the `gh attestation verify` command callers should run
  before installing.
- **`.github/workflows/codeql.yml`** — Python `security-and-quality`
  CodeQL scan on every push, PR, and weekly cron. Findings go to the
  repo's Security tab.
- **Zero-deps smoke job in `ci.yml`** — installs the built wheel into
  a fresh venv with no `[dev]` or `[otel]` extras and imports the
  full public surface on Python 3.11 / 3.12 / 3.13. Guards the
  zero-runtime-dependency promise against accidental optional-deps
  leakage in `__init__`.
- **`examples/durable_agent.py`** — full SqliteSaver + thread_id +
  idempotency_key + ProviderRouter + RateLimiter demo. Runs twice in
  a row to show resume-from-checkpoint replay.
- **`examples/parallel_tools.py`** — `arun_parallel_tools` under
  `asyncio.TaskGroup` with input-order results.
- **`examples/pricing.json` populated** — six representative 2025-Q4
  entries (premium / mid / mini tiers) with `_verified_on` timestamp
  and a demonstration of the new 5-min / 1-hour ephemeral
  cache-write tiers. Model identifiers are intentionally generic so
  callers can drop in their own provider names without diff noise.

### Added — Sprint 5 (usage limits, prompt caching TTL, scheduler, async policy, persistent sinks)

- **`UsageLimits`** — per-session token / tool-call / cost caps with
  Pydantic-AI-compatible field names (`request_tokens_max`,
  `response_tokens_max`, `total_tokens_max`, `tool_calls_max`,
  `cost_usd_max`). Wired into `AgentSession(usage_limits=...)`; each
  turn calls `tracker.check_limits` post-record.
- **`UsageLimitExceededError`** — distinct from `BudgetExceededError`.
  Both share the new `UsageBoundExceededError` base class so callers
  can choose to handle them together or separately.
- **`UsageSnapshot.cache_ttl`** and **`UsageSnapshot.tool_calls`** —
  optional ephemeral-cache TTL hint (`"5m"` / `"1h"` / `None`) and a
  per-turn tool-call counter for `tool_calls_max` accounting.
- **`ModelPricing.cache_write_5min_per_million` /
  `cache_write_1h_per_million`** — 2026 ephemeral prompt-caching tiers.
  `UsageTracker.cost_for_turn` picks the rate via
  `ModelPricing.write_rate_for_ttl`; unknown TTL falls back to the
  legacy single-tier `cache_write_per_million`.
- **`scheduler.py`** — `Clock` protocol, `SystemClock` (default
  production), `ManualClock` (canonical deterministic test double,
  promoted from `tests/conftest.py` with new `tick`, `now_utc`,
  `sleep_async` helpers).
- **`persistence.py`** — `SqliteEventSink` and `SqliteUsageSink`
  satisfy the existing `EventSink` / `UsageSink` protocols, persist
  to stdlib `sqlite3` in WAL mode, and survive process restart. Fills
  the long-running-session gap that the in-memory ring buffers can't.
- **`PolicyEngine.evaluate_async`** — awaits async `matches` while
  running sync conditions in place. `AsyncOrchestrationSession`
  callers can now plug coroutine-based policy rules in.

### Changed — Sprint 4 (OTel nesting + `AgentSession` rename)

- **`AgentSession` is the canonical class name** (formerly
  `Orchestrator`). The legacy `Orchestrator` symbol is now a bare
  alias for the same class — same constructor, same identity. It will
  be removed in 0.3.0; new code should import `AgentSession` directly.
- **`OpenTelemetrySink` now emits nested spans** instead of one-shot
  events. `AGENT_STARTED` / `PHASE_STARTED` open a long-lived parent
  span keyed by `(role, phase)`; subsequent events emit as children
  of that parent via OTel context propagation; `AGENT_COMPLETED` /
  `AGENT_FAILED` / `PHASE_COMPLETED` end it, copying the terminal
  event's attributes (incl. `error.type` and an `ERROR` status on
  failure) onto the parent. APM dashboards now see real trace trees
  per session instead of unrelated event roots. See
  [Migrating from 0.1.x](migrating-from-0.1.x.md).
- New `docs/migrating-from-0.1.x.md` walks the rename + the OTel wire
  format change.

### Added — Sprint 3 (rate limiting + routing + structured concurrency)

- **`TokenBucket` / `AsyncTokenBucket`** — classic token-bucket
  limiters with injectable clock. Sync uses `threading.Lock`; async
  uses `asyncio.Lock` + `asyncio.sleep` so waiting yields the event
  loop. `RateLimiter` / `AsyncRateLimiter` compose three named buckets
  (`rpm`, `input_tpm`, `output_tpm`) so typical LLM-provider limits
  fit in one object. `RateLimitExceededError` raised on timeout.
- **`Orchestrator(rate_limiter=...)` /
  `Orchestrator(async_rate_limiter=...)`** — wired into `run_turn` and
  `arun_turn`. RPM is spent before the call; input / output TPM after
  the `UsageSnapshot` is known, matching how providers enforce limits.
- **`ProviderRouter` protocol** with three reference implementations:
  `StaticProviderRouter` (wraps the existing `next_provider`),
  `RoundRobinProviderRouter` (strict rotation), `WeightedProviderRouter`
  (highest-weight non-excluded, ties to declaration order).
  `Orchestrator(provider_router=...)` exposes it on sessions; caller
  code consults it when a recovery step calls for a switch.
- **`RecoveryRecipe.step_retries`** — optional per-step retry budget
  the caller is expected to honor when executing a step. Default empty
  mapping preserves the 0.1.0 single-attempt semantics.
- **`AsyncOrchestrationSession.arun_parallel_tools(...)`** — runs a
  sequence of tool coroutines concurrently under `asyncio.TaskGroup`.
  Any child failure cancels its siblings and surfaces an
  `ExceptionGroup`; a `timeout` argument applies to the whole group.
- **`_ainvoke` migrated from `asyncio.wait_for` to `asyncio.timeout`** —
  proper structured-concurrency cancellation semantics per PEP 789.
  The inner task is cancelled exactly once; no resurrection.
- **Docs**: `docs/patterns/rate-limiting.md`, `docs/patterns/routing.md`,
  `docs/api/rate_limit.md`, `docs/api/routing.md`, all in the nav.

### Added — Sprint 2 (durable execution)

- **`CheckpointSaver` protocol** — `get` / `put` / `list` / `delete`
  shape that mirrors the LangGraph contract. Two reference impls ship:
  `InMemorySaver` (process-local) and `SqliteSaver(path)` (durable via
  stdlib `sqlite3`, no new runtime dependency). Both are thread-safe;
  `SqliteSaver` uses WAL mode so concurrent readers don't block the
  writer.
- **`Orchestrator(saver=...)` + `session(thread_id=...)` /
  `asession(thread_id=...)`** — pair the two to turn a session into a
  restart-resumable workflow. Per-turn checkpoints are written
  automatically when both are configured.
- **`run_turn(..., idempotency_key=...)` /
  `arun_turn(..., idempotency_key=...)`** — replay-safe turns. A
  matching key on the same `thread_id` short-circuits the call and
  returns the cached `(result, usage)` without invoking the model.
- **`docs/patterns/durability.md` + `docs/api/checkpoint.md`** — when /
  when-not / anti-patterns / tuning + mkdocstrings API reference.

### Fixed — Sprint 0 (code-quality + bug fixes)

- **Release pipeline gating** — `.github/workflows/release.yml` now
  runs `ruff` + `mypy --strict` + `pytest` + per-module coverage on
  3.11/3.12/3.13 BEFORE the PyPI publish step. Pre-0.2.0 the publish
  step could ship a wheel that failed CI on the underlying commit.
- **`_resolve_pricing` read race** — `usage_tracking._resolve_pricing`
  now snapshots `PRICING_TABLE` under `_pricing_lock` before the
  prefix-match loop, closing the `RuntimeError: dictionary changed
  size during iteration` window that opened whenever a thread called
  `register_pricing` while another resolved a model.
- **Hard turn timeout was blocking** — `OrchestrationSession._invoke_fn`
  no longer uses `with ThreadPoolExecutor(...) as ex:`, whose `__exit__`
  calls `shutdown(wait=True)` and made `TurnTimeoutError` wait for the
  slow worker thread to return. We now call `shutdown(wait=False,
  cancel_futures=True)` in finally so the timeout propagates promptly.
- **`classify_exception` walks the exception chain** — wrapped errors
  (`raise MyAppError() from ConnectionError(...)`) are now classified
  by the original cause's type. Cyclic chains are detected and broken.
  Type-based dispatch is consolidated into `_EXCEPTION_TYPE_MAPPING`.
- **`RecoveryRecipe.steps` is now `tuple[RecoveryStep, ...]`** — frozen
  dataclass contract is honored end-to-end. Construction from a `list`
  is still accepted (auto-converted in `__post_init__`) for back-compat.
- **`__version__` sourced from package metadata** — single source of
  truth in `pyproject.toml`; `importlib.metadata.version()` with a
  local-checkout fallback so editable installs keep working.
- **CI build matrix** — `.github/workflows/ci.yml` build job now runs
  on Python 3.11/3.12/3.13 (was 3.11 only), matching the test matrix.

### Fixed — Sprint 1 (docs trust)

- **`mkdocs.yml` site description** — "(alpha)" → "(beta)" to match the
  pyproject.toml classifier, README, and CHANGELOG.
- **`docs/index.md`** — added a `!!! warning "Beta"` admonition pointing
  at the migration guide so the landing page no longer reads as
  "Production".
- **`.github/workflows/docs.yml`** — `mkdocs build` → `mkdocs build
  --strict` so broken refs and unresolved nav entries fail the build
  instead of silently degrading the published site.
- **`CONTRIBUTING.md`** — new "Testing" section covering when to reach
  for Hypothesis property tests and how to use the `ManualClock`
  injection pattern from `tests/conftest.py`.
- **`CODEOWNERS`** — added an inline note flagging that the
  `@Techrevati/runtime-maintainers` team must exist on GitHub for
  auto-review to actually trigger.

## [Pre-0.2.0] — Sprint 6 (testing rigor)

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
  `run_turn` and `arun_turn`. Stopping conditions are an industry
  production-readiness requirement.
- `Handoff` immutable dataclass (`techrevati.runtime.handoffs`) +
  `OrchestrationSession.handoff_to(target_role, reason, context)`.
  Finalizes the source worker as COMPLETED, registers a fresh worker
  for the target role under the same project_id, returns a Handoff
  describing the delegation. Enables the orchestrator-workers
  delegation pattern on top of our primitives.
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
  vendor pricing (not normative).
- `docs/api/*.md` eight `mkdocstrings`-backed API reference pages
  via the Python handler.
- `docs/patterns/orchestrator.md` rewritten with
  When-to-use / Anti-patterns / Tuning template + a prominent
  naming-disambiguation callout separating our `Orchestrator` from
  the *orchestrator-workers* delegation pattern.
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
