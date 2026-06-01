# Release Candidate Inventory

Author: Techrevati doo

This inventory records the current `0.3.0rc1` preparation state. It is intended
to make the release candidate reviewable as one coherent body of work instead
of a long unstructured diff.

## Current Branch And Target

- Working branch: `codex/production-rc-0.3.0`.
- Current package version: `0.3.0rc1`.
- Target release candidate: `0.3.0rc1`.
- Target stable release: `0.3.0`.
- Release shape: Python runtime package.
- First production channel: private package registry or controlled internal
  artifact channel.
- Public package index publication: out of scope until the RC pilot succeeds.
- Hosted service wrapper: out of scope for `0.3.0`.

## Current Diff Shape

The tracked release candidate diff currently spans these groups:

| Group | Purpose | Review focus |
|---|---|---|
| Workflows and automation | CI, docs, release, code scanning, issue templates | permissions, action pinning, tag gates, timeouts |
| Package and policy metadata | package metadata, changelog, ownership, security docs | version consistency, authorship, support policy |
| Runtime modules | hardened primitives under `techrevati.runtime` | public API compatibility, validation, failure behavior |
| Tests | regression, integration, release, workflow, docs, package checks | coverage quality, brittle assumptions |
| Documentation | getting started, patterns, API reference, compliance pages | public branding, operator usefulness |
| Scripts | release, security, package, docs, source, workflow guard scripts | false positives, CI parity, maintainability |

At the last inventory pass, the tracked diff contained 85 modified tracked files
with approximately 8,859 insertions and 1,794 deletions. New untracked release
assets include API reference pages, compliance pages, docs styling/theme files,
guard scripts, and their tests. The untracked release asset count is currently
104 files.

## Review Order

Review in this order to keep the risk low:

1. Repository hygiene, generated files, docs theme, and ignored artifacts.
2. Branding and authorship.
3. Package metadata, changelog, version consistency, and release notes.
4. Workflow security and release automation.
5. Guard scripts and tests.
6. Runtime public API and compatibility.
7. Runtime hardening behavior by subsystem.
8. Documentation accuracy and operator readiness.
9. Full production gate evidence.

## Sprint 0 Checklist

- [x] Create the release candidate branch.
- [x] Add a production readiness plan to public documentation.
- [x] Add this release candidate inventory.
- [x] Add a final diff review checklist and guard.
- [x] Add an RC reviewer handoff checklist and guard.
- [x] Add a staging manifest checklist and guard.
- [x] Confirm strict documentation build.
- [x] Confirm public branding guard.
- [x] Confirm docs publication guard.
- [x] Add a remote CI validation checklist and guard.
- [x] Run the full production gate after docs changes.
- [x] Review untracked files and confirm each belongs in the RC.
- [x] Confirm ignored build/cache artifacts are not part of the RC.
- [x] Split accidental or out-of-scope files out of the RC.
- [ ] Stage files only after the RC inventory review is complete.

## Sprint 1 Checklist

- [x] Freeze the current package-level public export set.
- [x] Make the public API guard reject exports outside the frozen set.
- [x] Make the public API guard reject accidental export order changes.
- [x] Add regression tests for the frozen public API guard.
- [x] Verify deprecated compatibility surface tests still pass.
- [x] Review package-level documentation against the frozen API.
- [x] Add an automated guard for documented package-level imports.
- [x] Validate local dependency I/O failures classify as dependency failures
  rather than model failures.
- [x] Validate fallback caller validation failures classify as validation
  failures without masking context, dependency, timeout, or provider matches.
- [x] Validate provider/model prompt rejection failures classify as prompt
  rejections rather than generic model failures.
- [x] Validate runtime rate-limiter hard-stops classify as rate-limit failures
  rather than generic model failures.
- [x] Validate uncaught max-iterations hard-stops classify as governance
  breaches rather than generic model failures.
- [x] Validate caller-driven cancellation classifies as cancellation rather
  than an unknown failure.
- [x] Validate caller-driven cancellation remains typed in telemetry without
  setting OTel error status.
- [x] Validate event schema rejects inconsistent cancellation status and
  failure-class payloads.
- [x] Validate event schema rejects `agent.failed` payloads without
  `failure_class`.
- [x] Confirm no release candidate change requires a breaking-change note.

## Sprint 2 Checklist

- [x] Change package version from `0.3.0.dev1` to `0.3.0rc1`.
- [x] Add changelog entry for `0.3.0rc1`.
- [x] Allow tag-gated release validation for `v0.3.0rc1`.
- [x] Verify version consistency after reinstalling editable metadata.
- [x] Build wheel and sdist for `0.3.0rc1`.
- [x] Run distribution checks and metadata rendering checks.
- [x] Run zero-dependency wheel install smoke.
- [x] Run optional OTel import smoke.
- [x] Generate CycloneDX SBOM JSON and XML.
- [x] Confirm private registry publish steps are documented.
- [x] Add a private RC publication checklist and guard.
- [x] Add a release evidence verifier for checksum manifest integrity.

## Sprint 3 Checklist

- [x] Add a repository-wide secret leak guard.
- [x] Wire the secret leak guard into CI and release verification jobs.
- [x] Add regression tests for secret leak detection.
- [x] Harden the security pattern guard against literal subprocess shell
  bypasses, missing subprocess and HTTP client timeouts, implicit
  `subprocess.run` check semantics, raw exception text leakage, disabled TLS
  verification, and unsafe YAML loading.
- [x] Document deployment threat model and secret handling controls.
- [x] Add a pinned audit toolchain for dependency vulnerability scanning.
- [x] Wire dependency vulnerability scanning into CI and release verification.
- [x] Run dependency vulnerability scanning for all optional toolchains.
- [x] Make built-in model I/O logging metadata-only by default.
- [x] Make OTel event detail export opt-in.
- [x] Review logs, traces, diagnostics, and error handling for sensitive-data
  exposure.
- [x] Add an explicit pilot profile for guardrails, permissions, and
  governance.
- [x] Confirm guardrails, permissions, and governance defaults for pilot use.
- [x] Validate pilot profile denies unknown roles by default without changing
  base `PermissionPolicy` compatibility behavior.
- [x] Add a security review checklist and guard.

## Sprint 4 Checklist

- [x] Add a pilot operations runbook for version checks, event inspection,
  usage inspection, governance breaches, rollback, pilot shutdown, and
  diagnostic bundle collection.
- [x] Define required pilot signals, minimum alert rules, and retention policy.
- [x] Include terminal failure-class distribution in pilot signal review.
- [x] Add fan-out event and usage sinks so pilots can write durable local
  evidence and telemetry in the same session.
- [x] Add an automated operations runbook guard.
- [x] Wire the operations runbook guard into CI and release verification jobs.

## Sprint 5 Preparation Checklist

- [x] Add a controlled RC pilot evidence template for go/no-go review.
- [x] Add a rollback proof checklist for the downstream pilot environment.
- [x] Add a rollback execution checklist and guard.
- [x] Add a stable promotion checklist and guard.
- [x] Add a local pilot dry-run guard for the controlled scenario wiring.
- [x] Add a pilot execution checklist and guard.
- [x] Add an RC readiness summary and guard for release decision boundaries.
- [x] Cover required controlled scenarios, pilot signals, no-go conditions, and
  stable-release success criteria in the evidence template.
- [x] Cover expected terminal failure classes for permission, guardrail, and
  governance stops in pilot evidence.
- [x] Add an automated pilot evidence guard.
- [x] Wire the RC readiness, stable promotion, pilot dry-run, and pilot
  evidence guards into CI and release verification jobs.
- [ ] Execute the real controlled RC pilot.
- [ ] Test rollback in the pilot environment.

## Known Release Candidate Risks

| Risk | Status | Mitigation |
|---|---|---|
| Large diff is hard to review | Mitigated | Guarded final diff review checklist and subsystem review order |
| Final reviewer handoff may miss blockers | Mitigated | Guarded reviewer handoff checklist with release evidence, external blockers, and no-go rules |
| Untracked RC assets may be missed during staging | Mitigated | Guarded staging manifest categorizes untracked release assets before stage/tag |
| New guard scripts may be too strict | Mitigated | Guard calibration checklist and false-positive procedure before stable |
| Source hygiene may miss guard scripts or tests when run with default settings | Mitigated | Source hygiene scans `src`, `scripts`, and `tests` by default; skips generated/cache directories; allows `print()` only under `scripts/` while keeping debug breakpoints, `pdb`, bare exceptions, `NotImplementedError` runtime stubs, implicit text-file encodings, and stale markers blocked |
| Public API may have accidental surface changes | Mitigated | Frozen API and documented import guards in Sprint 1 |
| Public handoff hooks may appear available without running | Mitigated | `before_handoff` now runs before target worker registration, with sync and async regression tests |
| Governance scope labels may imply unsupported cross-session enforcement | Mitigated | Governance limits now fail closed to `session` scope until thread/project enforcement exists |
| Session-scoped governance counters may leak across sessions | Mitigated | `AgentSession` now creates a fresh governance state for every sync and async session |
| Governance hard-stops may be misclassified as dependency failures | Mitigated | Dedicated `governance_breach` failure class covers breach and terminal failure events |
| Permission or guardrail terminal failures may be misclassified | Mitigated | Dedicated `permission_denied` and `guardrail_violation` failure classes cover policy blocks |
| Local persistence or I/O failures may be misclassified as model failures | Mitigated | `OSError`, database, SQLite, disk, and filesystem failures now classify through dependency failure paths with retry and checkpoint regression coverage |
| Caller validation failures may be misclassified as model failures | Mitigated | Fallback `ValueError` and `TypeError` terminal failures now use `validation_error` after more specific classifier matches are checked |
| Prompt/content-policy rejections may be misclassified as generic model failures | Mitigated | Prompt rejection markers now map terminal failures to `prompt_rejection` before validation fallback |
| Runtime rate-limiter hard-stops may be misclassified as model failures | Mitigated | `RateLimitExceededError` terminal failures now use `rate_limit` in sync and async session paths |
| Max-iterations hard-stops may be misclassified as model failures | Mitigated | Uncaught `MaxIterationsExceededError` terminal failures now use `governance_breach` in sync and async session paths |
| Caller-driven cancellation may be counted as unknown failure | Mitigated | Sync and async cancellation now use `cancelled` in terminal failure-class telemetry |
| Caller-driven cancellation may inflate OTel error-rate alerts | Mitigated | Cancellation spans keep `techrevati.failure_class=cancelled` without `error.type` or `StatusCode.ERROR` |
| Cancellation payloads may disagree on status and failure class | Mitigated | `AgentEvent` rejects inconsistent `agent.failed` cancellation status/failure-class combinations |
| Failed audit payloads may omit failure taxonomy | Mitigated | `AgentEvent` rejects `agent.failed` payloads without a valid `failure_class` |
| Production plan and pilot evidence may lag runtime audit semantics | Mitigated | Production readiness, security, pilot evidence, operations, and pattern docs now require terminal failure-class evidence and failure-class distribution review |
| Committed secrets may leak through non-Python files | Mitigated | Repository-wide secret leak guard in Sprint 3 |
| Disabled TLS verification, subprocess shell bypasses, subprocess or HTTP client calls without timeouts, raw logging tracebacks, raw exception text, or unsafe YAML loading may enter source, scripts, or tests | Mitigated | Security pattern guard scans `src`, `scripts`, and `tests` by default and rejects `verify=False`, literal `verify=0`, literal truthy `shell=...`, missing subprocess timeouts, implicit `subprocess.run` check semantics, direct `subprocess.Popen`, missing HTTP client timeouts, literal `exc_info=True`/`stack_info=True`, raw exception text in logging/observability payloads, unsafe PyYAML helpers, and unsafe `yaml.load`/`yaml.load_all` usage |
| Security review may miss runtime-specific risks | Mitigated | Guarded security review checklist for runtime, supply-chain, workflow, pilot, and rollback risks |
| Pilot workflows may omit required safety primitives | Mitigated | Explicit pilot profile helper in Sprint 3 |
| Unknown pilot roles may bypass the allowed-tool policy | Mitigated | `build_pilot_profile` sets `PermissionPolicy(default_allow_unknown_roles=False)` with regression coverage |
| Pilot telemetry may be incomplete | Mitigated | Guarded operations runbook and fan-out sinks in Sprint 4 |
| Pilot go/no-go evidence may be incomplete | Mitigated | Guarded evidence template and rollback checklist in Sprint 5 prep |
| Private RC publication may publish wrong artifacts or channel | Mitigated | Guarded private RC publication checklist, staged artifact verification, and private-channel workflow controls |
| Local dry-run may be mistaken for real pilot evidence | Mitigated | Guarded pilot execution checklist keeps dry-run setup evidence separate from real downstream pilot evidence |
| Green local/server gate may be mistaken for stable readiness | Mitigated | RC readiness summary and guard keep stable blockers explicit |
| Stable promotion may start before external evidence is complete | Mitigated | Guarded stable promotion checklist requires fresh external evidence before `0.3.0` |
| Remote CI evidence may mismatch the reviewed commit | Mitigated | Guarded remote CI validation checklist for commit parity, required jobs, evidence, and triage |
| RC has not run in a real pilot workflow | Open | Controlled pilot before `0.3.0` |
| Remote CI has not yet validated the final commit | Open | Require green remote CI before tagging |
| Rollback is not yet proven in pilot environment | Open | Execute the guarded rollback execution checklist during Sprint 5 |

## Current Gate Evidence

The latest full production gate passed with:

- 999 tests passed.
- Total coverage: 94.85 percent.
- Per-module coverage floor passed at 85 percent.
- Strict typing passed.
- Lint and format checks passed.
- Strict documentation build passed.
- Public branding guard passed.
- Final diff review guard passed.
- RC reviewer handoff guard passed.
- Staging manifest guard passed.
- Guard calibration guard passed.
- Source hygiene full-scope default, generated/cache directory skip,
  abstract-method stub, text I/O encoding, and CLI-script output calibration
  passed.
- Pilot execution guard passed. Branch, base HEAD, working-tree diff, and
  untracked release-asset snapshot parity passed.
- Private RC publication guard passed.
- Staged private package artifact verification passed.
- Handoff hook wiring, mutation, blocking, and async dispatch tests passed.
- Governance scope fail-closed validation passed.
- Fresh per-session governance state validation passed.
- Pilot unknown-role fail-closed validation passed.
- Governance breach failure-class validation passed.
- Permission and guardrail terminal failure-class validation passed.
- Local dependency I/O and checkpoint persistence failure-class validation
  passed.
- Caller validation failure-class validation passed.
- Prompt rejection failure-class validation passed.
- Runtime rate-limiter terminal failure-class validation passed.
- Max-iterations terminal failure-class validation passed.
- Cancellation terminal failure-class validation passed.
- Cancellation OTel non-error validation passed.
- Cancellation schema consistency validation passed.
- Failed-event failure-class schema validation passed.
- Production readiness, security, pilot evidence, operations, and pattern docs
  align with the terminal failure taxonomy.
- Pilot and operations evidence now require failure-class distribution review.
- RC readiness guard passed.
- Stable promotion guard passed.
- Stable promotion guard rejects stable `0.3.0` without approved external
  evidence.
- Release workflow checksum manifest guard passed.
- Release evidence verifier passed.
- Production gate release-evidence parity guard passed.
- Local release-evidence smoke isolation requirement passed.
- Remote CI validation guard passed.
- Rollback execution guard passed. Branch, base HEAD, working-tree diff, and
  untracked release-asset snapshot parity passed.
- Secret leak guard passed.
- Security pattern guard full-scope default passed, including literal truthy
  subprocess shell bypasses, missing subprocess timeouts, direct
  `subprocess.Popen`, implicit `subprocess.run` check semantics, missing HTTP
  client timeouts, disabled TLS verification, raw logging traceback checks, raw
  exception text checks for logging/observability payloads, and unsafe YAML
  loading checks.
- Security review guard passed.
- Dependency vulnerability guard passed for dev, docs, build, release, and OTel
  dependency sets.
- Distribution build and metadata checks passed.

The latest Sprint 2 artifact pass built and verified:

- `techrevati_runtime-0.3.0rc1-py3-none-any.whl`.
- `techrevati_runtime-0.3.0rc1.tar.gz`.
- `sbom.cyclonedx.json`.
- `sbom.cyclonedx.xml`.
- Clean wheel install with `--no-index --no-deps`.
- Optional OTel import smoke.

This evidence is necessary but not sufficient for stable production readiness.
The stable release still requires final diff review, remote CI, security review,
private RC publication, pilot evidence, and rollback validation. It also
requires stable promotion approval.

## Untracked File Review

The untracked files visible in the release candidate working tree are expected
release assets:

- API reference pages for newly documented modules.
- Compliance pages, including this inventory and the production readiness plan.
- Documentation theme and style files.
- Guard scripts for changelog, CI, docs, package, release, repository, source,
  security, workflow, and version policy checks.
- Tests for those new guards and release checks.

Ignored local artifacts such as `dist`, `.venv`, `__pycache__`, and tool caches
are covered by `.gitignore` and do not appear in `git status --short
--untracked-files=all`. They must remain unstaged.

## Debt Payment Rule

Any issue found during inventory must be handled in one of three ways:

- fix it immediately with a regression test,
- document it as an explicit RC-blocking or non-blocking backlog item, or
- remove it from the release candidate scope.

No hidden production risk should be carried forward from Sprint 0.
