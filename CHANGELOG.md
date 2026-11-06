# Changelog

Author: Techrevati doo

## 0.5.0 - 2026-11-06

The hardening line: every defect below was reproduced by an executed proof before
it was fixed. See [Migrating from 0.4.x](migrating-from-0.4.x.md) for what changes
for existing callers.

Added:

- `run_tool` / `arun_tool` accept `args`, `timeout` (`ToolTimeoutError`), and
  `idempotency_key`, which replays a completed tool call instead of repeating its
  side effect (at-least-once).
- `MaxRepeatedToolCallsLimit` stops an agent repeating the same tool call.
- `run_turn_with_fallback` / `arun_turn_with_fallback` drive `provider_router`.
- `arun_turn_stream(idempotency_key=...)`.
- `session(handoff=...)` / `asession(handoff=...)` run on the worker a `Handoff`
  reserved.
- `agent.turn.completed` / `agent.turn.failed` events, `chat` spans,
  `OpenTelemetryMetricsSink`, and the `gen_ai.client.inference.usage.*` counters.
- MCP Python SDK 2.x support alongside 1.x, plus a per-call `timeout`.
- Audit log `tip()`, `ChainTip`, and `verify_chain(expected_tip=...)`.
- `AgentRegistry.evict_terminal()`, `TokenBucket.debit()`.
- Audit purge markers, signed under the sink's key.
- `AgentSession(audit_fail_closed=True)` refuses further actions with
  `AuditWriteError` once the audit log cannot be written.
- Python 3.14 support.

Fixed, security and audit integrity:

- Two audit writers on one SQLite file could wedge it and silently lose every later
  record. Appends are now atomic and writers serialize into one chain.
- Quadratic regular expressions in `PromptInjectionGuardrail` and `RedactPIIHook` let
  untrusted content stall the event loop.
- Prompt-injection and pattern guardrails were bypassed by any structured output,
  because `str()` escaped the newlines their patterns match on.
- A role name differing by case or an invisible character bypassed its deny list.
- Async policy conditions always matched under `And` / `Or` and in sync `evaluate`.
- The human-oversight gate accepted replayed, stale, spoofed, and invalid decisions,
  and did not enforce its timeout on a hanging queue.
- The oversight trail omitted the reason and action, and recorded nothing if a queue
  raised. The compliance kit's log now receives oversight events.
- Unserializable event content dropped audit records while the action proceeded, and
  could suppress incident notification. Hashes of existing records are unchanged.
- A deleted audit tail was not detected on a live sink.
- Tampered rows crashed verification rather than reporting invalid. CSV export did
  not escape timestamps. Purge compared timestamp strings across UTC offsets.
- PII redaction skipped tuples, sets, dict keys, and bytes, and missed standard base64
  keys and JWT headers. Combined patterns lost backreferences.

Fixed, accounting and durability:

- A turn that crossed a budget or usage limit, or whose `after_model` hook failed, was
  missing from the checkpoint and from governance cost, so the retry paid again and
  `MaxBudgetLimit` could never trip.
- A replayed turn did not restore its spend, so a restart reset the budget.
- Usage above a rate-limit bucket's capacity raised after the model call and lost the
  turn.
- Usage passed without `model=` was discarded, which disabled `UsageLimits`.
- A failed SQLite write left a transaction open: every later write failed and reads
  went stale until restart (`SqliteSaver`, `SqliteEventSink`, `SqliteUsageSink`).
- Concurrent cold starts of a new SQLite file crashed with `IntegrityError`.
- JSON-valid state with a lone surrogate failed on `SqliteSaver` and aborted the turn.
- Pricing prefixes had no name boundary: `gpt-4` priced `gpt-4o-mini` and suppressed
  the missing-price warning.
- Cache writes could cost $0 on a tiered model. Float sums could falsely exceed a
  budget at its exact boundary.

Fixed, runtime behaviour:

- `arun_turn_stream(timeout=)` cancelled the consumer's task, even after the consumer
  had left the stream, because the timeout scope spanned a `yield`.
- A hung half-open probe blocked the circuit indefinitely, a stale probe result could
  close it, and it could stay half-open under sustained load despite successes.
- Context-overflow errors containing a token count such as `14290` were classified as
  rate limits, and digits inside identifiers as HTTP statuses.
- Duplicate names in a router's `exclude` made every router return `None`.
- A timed-out `pause_for_input` stranded the worker and made session exit raise.
- `run_turn(timeout=)` lost context variables, including tracing context, in its
  worker thread. A `TimeoutError` raised by user code was reported as the deadline.
- Compaction identified messages by object identity, so a reused instance defeated
  both limits, and it could delete the newest message.
- `JsonOutputSpec` leaked `RecursionError` and `ValueError`, and accepted `NaN`.
- Orphan spans were lost under a batch processor at exit. `OpenTelemetrySink.shutdown()`
  closes them explicitly.
- `execute_tool` spans lacked `gen_ai.tool.name`, tool spans stayed open after a
  post-guardrail block or cancellation, and cache tokens never reached metrics.
- Runtime exceptions did not survive `pickle` or `copy`.
- `mkdocs build` failed with current `pymdown-extensions`; `mkdocstrings` is updated.

Changed:

- **Breaking:** `default_allow_unknown_roles` now defaults to `False`, so a role with no
  config is denied.
- A turn timeout now counts as a circuit-breaker failure, so a provider that hangs
  opens the circuit.
- Toolchain updated (ruff 0.17, mypy 2.4, pytest 9.2, hypothesis, OpenTelemetry 1.45,
  and pinned actions for checkout, setup-python, and the release publisher).

## 0.4.0 - 2026-06-20

The EU AI Act compliance line.

Added:

- `techrevati.runtime.compliance` subpackage with EU AI Act (Regulation (EU)
  2024/1689) technical primitives:
  - `AuditLogSink` — tamper-evident, hash-chained event + usage log with
    `verify_chain()` (optional `require_genesis=True` front-truncation check),
    optional HMAC envelope, JSONL/CSV export, retention purge, and
    `SqliteAuditBackend` / `InMemoryAuditBackend` (Article 12).
  - `HumanOversightInterface`, `ReviewQueue`, `ReviewerIdentity`,
    `ExplanationReport` — pause / review / override with audit trail (Article 14).
  - `RiskRegistry`, `Risk`, `ResidualRiskLevel` — residual-risk register that
    blocks deployment on `UNACCEPTABLE` (Article 9).
  - `OutputIntegrityGuardrail` + `InputSanitizationHook` — control/escape-byte
    integrity checks on tool I/O (Article 15).
  - `IncidentReportingSink`, `SeriousIncidentDetector`, `IncidentReport` —
    incident detection with 15-day reporting-deadline tracking (Articles 26/73).
  - `TransparencyReport`, `AccuracyDeclaration` — instructions-for-use rendering
    (Article 13).
  - `EUAIActComplianceKit` facade + `ConformityChecklist` (Article 16 self-check).
- `AgentSession(audit_log=...)` and `AgentSession(compliance=kit)` — fan audit /
  incident sinks alongside the caller's sinks, prepend Article 15 guardrails /
  hooks, and assert no unacceptable residual risk before a session opens.
- `oversight.review_requested` / `oversight.review_resolved` event names.
- `docs/eu-ai-act/` article-by-article guidance (with the audit-log threat model)
  and an expanded compliance crosswalk.
- Optional `[mcp]` extra + `techrevati.runtime.mcp` module: `MCPToolAdapter`
  bridges a connected `mcp.ClientSession`'s tools into `arun_tool` as coroutine
  factories (no tool registry; permission/guardrail/governance/hook checks still
  apply). Core stays zero-dependency.
- Typed outputs: `OutputSpec[T]` protocol + `JsonOutputSpec`, `RegexOutputSpec`,
  `CallableOutputSpec`, and `OutputValidationError` for validating raw model text
  into typed values.
- Session memory: `ConversationMemory` protocol + `InMemoryConversationMemory`,
  `MemoryMessage`, and compaction strategies (`NoCompaction`, `WindowCompaction`,
  `TokenBudgetCompaction`).
- Step-level durability: `StepCheckpointSaver` (implemented by `InMemorySaver` and
  `SqliteSaver`) with `put_step` / `get_step` / `list_steps` + `StepRecord` for
  in-tool-call replay (caller-keyed memoization, not deterministic replay).
- OTel GenAI message bodies: `OpenTelemetrySink` emits caller-supplied
  `gen_ai.input.messages` / `gen_ai.output.messages` (from `AgentEvent.data`) as
  span events, gated on `include_event_detail`. (The `gen_ai.client.token.usage`
  metric and per-tool span nesting already shipped in 0.3.0.)
- Optional `[postgres]` and `[redis]` extras + durability recipe docs for
  implementing `CheckpointSaver` / `AuditBackend` against PostgreSQL and Redis
  (the zero-dependency core continues to ship SQLite reference savers only).

Removed:

- The deprecated `Orchestrator` compatibility alias (use `AgentSession`). It has
  emitted a `DeprecationWarning` since 0.2.1; see
  [docs/migrating-from-0.3.x.md](migrating-from-0.3.x.md).

## 0.3.0rc1 - 2026-05-31

Release candidate for the 0.3.0 line.

Added:

- Governance plane with hard-stop and alert-only limits.
- Async guardrails.
- Streaming turn support through `arun_turn_stream`.
- Mutating hook chain for model and tool inputs/outputs.
- Built-in hooks for redaction, model I/O logging, and token-budget checks.
- Pattern and API documentation for governance, streaming, and hooks.
- Release, documentation, workflow, package, public API, and security guard
  scripts for repeatable release-candidate validation.
- Explicit pilot profile helper for controlled release-candidate workflows.
- Public `resolve_pricing(model)` helper (exact-then-longest-prefix model
  pricing lookup, zero-fallback) so callers no longer reach into a private
  symbol.
- Fan-out event and usage sinks for combining durable local evidence with
  telemetry exporters.
- Pilot operations runbook and guard for required signals, alerts, retention,
  rollback, shutdown, and diagnostic bundle collection.
- Controlled RC pilot evidence and rollback proof templates, with a guard that
  keeps required scenarios, signals, no-go conditions, and success criteria
  present before stable release.
- Local pilot dry-run guard that exercises pilot wiring for success, guardrail,
  permission, governance, provider-failover, checkpoint, sink-failure, and
  rollback-readiness scenarios.
- RC readiness summary and guard that keep private-RC and stable-release
  decision boundaries explicit before tagging or promotion.
- Final diff review checklist and guard that keep subsystem review scope,
  no-go rules, and sign-off evidence explicit for the release candidate.
- Guard calibration checklist and guard that keep release guard inventory,
  CI/release wiring, and false-positive handling explicit before stable
  promotion.
- Private RC publication checklist, workflow controls, and guard to keep
  release-candidate package publication limited to the configured private
  channel before pilot approval.
- Security review checklist and guard covering runtime boundaries,
  supply-chain evidence, secret/data exposure, workflow release controls, pilot
  controls, and no-go rules.
- Remote CI validation checklist and guard to keep same-commit hosted workflow
  evidence separate from local/server gate evidence before tagging.
- RC reviewer handoff checklist and guard that collect release evidence,
  remaining external blockers, and no-go rules before stage, tag, or publish.
- Staging manifest checklist and guard that classify untracked RC assets and
  keep generated files out before stage, tag, or publish.
- Pilot execution checklist and guard that keep local dry-run output separate
  from real downstream pilot evidence before stable promotion.
- Rollback execution checklist and guard that keep command-shape rollback
  checks separate from real downstream rollback evidence.
- Stable promotion checklist and guard that keep stable `0.3.0` verification
  blocked until external evidence is complete, fresh, and approved.
- Release checksum manifest attached as `SHA256SUMS` so wheel, source archive,
  and SBOM evidence can be verified before pilot or stable promotion.
- Release evidence verifier for wheel, source archive, SBOM files, and
  checksum manifest integrity.
- Production gate documentation now keeps release evidence smoke parity with
  the release workflow.
- Release evidence install smoke now force-reinstalls from the built wheel so
  an already installed package cannot hide a broken artifact.
- Local/server release-evidence smoke is documented to use an isolated
  temporary virtual environment so the editable test environment stays intact.

Changed:

- `AgentSession` is the canonical session factory name; `Orchestrator` remains
  available as a deprecated compatibility alias.
- Public documentation and package-level exports are frozen for the release
  candidate.
- Release artifacts now require verified wheel/sdist metadata and SBOM output.
- Release verification documentation now uses `dist/`-relative artifact paths
  so repository-root copy/paste checks verify the intended evidence set.
- Private RC publication now requires a signed security review before the RC
  tag or private publish step.
- Release evidence verification now parses SBOM JSON and XML and rejects
  non-CycloneDX or malformed SBOM files.
- Release evidence verification now rejects mismatched wheel/source archive
  package identity or version before private publication.
- Release evidence verification now reads wheel and source archive metadata so
  renamed artifacts with stale internal metadata are rejected.
- Release workflow now verifies staged private package upload artifacts before
  publishing to the private package channel.
- Security pattern checks now scan `src`, `scripts`, and `tests` by default,
  rejecting disabled TLS verification, literal truthy subprocess shell
  bypasses, missing subprocess timeouts, implicit `subprocess.run` check
  semantics, direct `subprocess.Popen` usage, raw logging tracebacks, missing
  HTTP client timeouts, raw exception text in logging/observability payloads,
  and unsafe PyYAML loading helpers.
- Source hygiene checks now scan `src`, `scripts`, and `tests` by default,
  skipping generated/cache directories and allowing intentional CLI script
  output while still rejecting debug
  breakpoints, `pdb`, bare exceptions, `NotImplementedError` runtime stubs,
  implicit text-file encodings, and stale markers.
- Handoff hooks now run before target worker registration, and async sessions
  expose `ahandoff_to` for async handoff hooks.
- Governance limit scopes now fail closed to supported `session` scope instead
  of accepting future `thread` or `project` scopes without cross-session
  enforcement.
- `PermissionPolicy` now accepts `default_allow_unknown_roles`; the base default
  remains compatibility-first, while the pilot profile uses fail-closed
  unknown-role behavior.
- `AgentSession` now gives each sync and async session a fresh governance state
  so session-scoped counters cannot leak across sessions from the same factory.
- Governance hard-stops now use a dedicated `governance_breach` failure class
  instead of being reported as dependency failures.
- Permission denials and guardrail blocks now use dedicated terminal failure
  classes instead of falling through generic exception classification.
- Validation errors, prompt/content-policy rejections, runtime rate-limit
  stops, max-iterations stops, and caller-driven cancellations now use explicit
  terminal failure classes instead of being grouped into generic model or
  unknown failures.
- Caller-driven cancellation remains visible in OTel telemetry without setting
  `error.type` or `StatusCode.ERROR`, preventing intentional stops from
  inflating operational error-rate alerts.
- Built-in model I/O logging and OTel event detail export are metadata-only by
  default unless callers explicitly opt in to sensitive detail capture.

Fixed:

- Hardened runtime validation and copy semantics across events, hooks,
  guardrails, governance, routing, rate limiting, retry, persistence,
  telemetry, streaming, and usage tracking.
- Removed non-author public branding from the package, documentation, and
  release metadata.

## 0.2.1 - 2026-05-20

Patch release for recovery, telemetry cleanup, pricing registration, and
guardrail reporting.

Fixed:

- Recovery recipes now honor per-step retry counts.
- Parent telemetry spans are closed on abrupt interpreter exit.

Added:

- `register_pricing(..., on_conflict=...)`.
- `PricingAlreadyRegisteredError`.
- `GuardrailViolation`.
- Multi-violation reporting on `GuardrailViolatedError`.
- Deprecation warning for the compatibility `Orchestrator` alias.

## 0.2.0 - 2026-05-20

Beta release with durable execution, rate limiting, routing, usage limits,
checkpointing, persistent sinks, and release hardening.

Added:

- `CheckpointSaver`, `InMemorySaver`, and `SqliteSaver`.
- Token-aware rate limiting.
- Provider routing.
- `UsageLimits` and usage-limit exceptions.
- Persistent SQLite event and usage sinks.
- Async policy evaluation.
- `AgentSession` as the canonical session factory name.

## Earlier Releases

Earlier `0.0.x` and `0.1.x` releases introduced the initial runtime primitives:
orchestrator sessions, retry policy, circuit breakers, usage tracking, quality
gate, lifecycle events, permissions, guardrails, handoffs, sinks, and optional
telemetry integration.
