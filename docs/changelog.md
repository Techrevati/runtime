# Changelog

Author: Techrevati doo

## 0.4.0.dev0 - 2026-06-02

The EU AI Act compliance line. Development build on `feat/0.4.0`.

Added:

- `techrevati.runtime.compliance` subpackage with EU AI Act (Regulation (EU)
  2024/1689) technical primitives:
  - `AuditLogSink` — tamper-evident, hash-chained event + usage log with
    `verify_chain()`, optional HMAC envelope, JSONL/CSV export, retention purge,
    and `SqliteAuditBackend` / `InMemoryAuditBackend` (Article 12).
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
