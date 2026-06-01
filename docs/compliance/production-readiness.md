# Production Readiness Plan

Author: Techrevati doo

This plan defines the path from the current hardened runtime state to a
production-ready `techrevati-runtime` release. The first production milestone is
`0.3.0rc1` as a private package release with repository release artifacts, SBOM,
full CI evidence, and a controlled pilot. The stable `0.3.0` release follows
only after the release candidate has passed pilot acceptance criteria.

## Definition of 100% Production Readiness

For this package, "100% production ready" means the release is ready to be used
as a dependency in production agent workflows. It does not mean zero risk, and
it does not include building a hosted API service around the runtime.

A release is production ready only when all of these are true:

- The package builds repeatably as wheel and sdist.
- The same commit is green in local/server gates and remote CI.
- Public branding is limited to Techrevati doo.
- The technical namespace remains `techrevati.runtime`.
- Public API compatibility is frozen for the release line.
- Session-scoped governance limits use fresh state per session and fail closed
  for unsupported cross-session scopes.
- Controlled pilot permissions deny unknown roles by default while the base
  permission policy keeps compatibility behavior unless callers opt into
  fail-closed unknown-role handling.
- Terminal audit taxonomy distinguishes runtime rate limits, budget and usage
  limits, governance breaches, permission denials, guardrail violations, tool
  errors, and dependency failures, including local persistence, I/O,
  validation failures, and prompt rejections.
- Security, source, repository, workflow, release, and package policy guards
  pass.
- Total coverage is at least 90 percent and every runtime module is at least
  85 percent covered.
- Documentation builds with `mkdocs build --strict`.
- Release artifacts pass distribution and metadata checks.
- A CycloneDX SBOM is produced for release artifacts.
- A private registry or controlled artifact channel is available.
- A pilot workflow has completed without unresolved P0/P1 issues.
- Operators have a runbook for monitoring, incident response, and rollback.
- The stable promotion record is approved with fresh external evidence.

## Baseline State

At plan creation, the runtime is already significantly hardened:

- The package is a Python library/runtime, not a hosted service.
- Runtime package name: `techrevati-runtime`.
- Current release-candidate version: `0.3.0rc1`.
- Runtime dependency policy: zero required runtime dependencies.
- Supported Python versions: 3.11, 3.12, and 3.13.
- Existing gates cover lint, formatting, strict typing, tests, coverage,
  package metadata, docs, branding, CI hardening, workflow pinning, release
  checks, public API checks, full-scope source hygiene for source, scripts,
  tests, generated/cache directory skips, abstract-method stubs, and
  deterministic text I/O encodings, and full-scope security patterns for
  source, scripts, and tests,
  including literal truthy subprocess shell bypasses, missing subprocess
  timeouts, implicit `subprocess.run` check semantics, direct
  `subprocess.Popen`, missing HTTP client timeouts, disabled TLS verification,
  raw logging traceback checks, raw exception text checks for
  logging/observability payloads, and unsafe YAML loading checks.
- Current local/server evidence is green at 999 tests with 94.85 percent total
  coverage, including dedicated regression coverage for session governance
  state, dependency I/O classification, rate-limit hard-stops, max-iterations
  hard-stops, cancellation, pilot unknown-role denial, and terminal
  failure-class taxonomy.
- The working tree contains a large uncommitted hardening set and must be
  reviewed as one release candidate body of work before tagging.

## Deep Assessment - 2026-06-01

The runtime is technically strong enough to stop broad local hardening and move
to evidence closure. More local guard work is still useful when it removes a
specific release risk, but it should no longer displace final review, remote CI,
private RC publication, pilot, or rollback proof.

Current realistic ratings:

| Area | Rating | Evidence | Remaining gap |
|---|---:|---|---|
| Runtime behavior and API | 91 / 100 | Typed primitives, frozen public API guard, compatibility tests, terminal failure taxonomy | Final reviewer must confirm the large behavior diff |
| Local quality gate | 94 / 100 | 999 tests, 94.85 percent total coverage, module floor, lint, format, strict typing | Same commit still needs remote CI proof |
| Release engineering | 88 / 100 | Wheel/sdist build, distribution checks, SBOM flow, release evidence guards, private publication controls | Private RC has not been tagged or published |
| Security posture | 86 / 100 | Secret scan, dependency audit, workflow hardening, action pinning, strict security/source guards | Human security sign-off and external CI evidence remain open |
| Documentation and operator readiness | 84 / 100 | Strict docs build, public branding guard, operations, pilot, rollback, and promotion docs | Runbooks have not yet been exercised by a real pilot operator |
| External production proof | 45 / 100 | Local pilot dry-run and checklists exist | No real controlled pilot, rollback execution, or stable promotion evidence yet |

Overall current assessment:

- Local/server technical readiness: 91 / 100.
- Private RC readiness: 86 / 100.
- Stable production readiness: 78 / 100.

This is a release candidate with a very strong local gate, not yet a stable
production release. The stable score is held down by missing external evidence,
not by a known local code defect.

The highest-risk items now are:

- the large unstaged release-candidate diff still needs final subsystem review,
- remote CI has not yet proven the same reviewed commit,
- the security review is guarded but not signed off,
- the private package channel has not published the RC artifact,
- the runtime has not run in a real controlled pilot workflow,
- rollback has not been proven in the pilot environment.

## Corrected Forward Plan

From this point, prioritize evidence closure over additional broad hardening.
Only accept new local code or guard changes when they fix a discovered bug,
close a release blocker, or reduce a concrete P0/P1 production risk.

1. Freeze the local release-candidate diff.
   - Stop opportunistic feature work.
   - Keep only bug fixes, guard fixes, docs accuracy fixes, and release
     evidence fixes in scope.
   - Re-run the full production gate after any change.

2. Complete final diff review and staging review.
   - Review the 85-file tracked diff by subsystem.
   - Review all 104 untracked release assets with
     `git status --short --untracked-files=all`.
   - Confirm generated/cache artifacts remain ignored and unstaged.
   - Complete `docs/compliance/final-diff-review.md`,
     `docs/compliance/rc-review-handoff.md`, and
     `docs/compliance/staging-manifest.md`.

3. Prove remote CI parity.
   - Push the reviewed branch or open the release-candidate PR.
   - Require CI, docs, release verification, workflow hardening, action
     pinning, security, source, package, and distribution guards on the same
     commit.
   - Record workflow run URLs and commit SHA in
     `docs/compliance/remote-ci-validation.md`.

4. Complete security review sign-off.
   - Use `docs/compliance/security-review.md`.
   - Resolve all high or critical findings before tagging.
   - Document any accepted lower-severity risk with owner, mitigation, and
     follow-up version.

5. Publish the private RC.
   - Tag `v0.3.0rc1` only after local/server gate, remote CI, review handoff,
     and security review are complete.
   - Let the release workflow build wheel, sdist, SBOM JSON, SBOM XML, and
     checksum manifest.
   - Verify repository release evidence and private package artifacts before
     pilot use.

6. Run one controlled RC pilot.
   - Use exactly one bounded workflow with known operators and limited volume.
   - Enable permissions, guardrails, governance limits, durable event/usage
     sinks, and telemetry.
   - Collect terminal failure-class distribution, cost, token usage, guardrail
     blocks, permission denials, governance breaches, sink failures, and
     provider failover evidence.

7. Prove rollback in the pilot environment.
   - Identify the previous known-good package version.
   - Install it from the controlled artifact source.
   - Run the rollback validation commands and record the evidence.

8. Decide stable promotion.
   - Promote only if the pilot has zero P0 incidents, zero unresolved P1
     incidents, complete rollback proof, approved stable promotion record, and
     green final local/server plus remote CI gates.
   - If pilot fixes are needed, cut `0.3.0rc2` instead of promoting a known
     flawed RC.

## Production Strategy

Use a phased strategy:

1. Freeze and inventory the current hardening work.
2. Lock public API and compatibility for `0.3.0`.
3. Prepare `0.3.0rc1` release engineering.
4. Run a lightweight security and compliance pass.
5. Add operations evidence: monitoring, alerts, and rollback.
6. Publish a private release candidate.
7. Run one controlled production pilot.
8. Fix only RC bugs.
9. Promote to stable `0.3.0`.
10. Stabilize for 7 to 14 days after production use begins.

The recommended release channel order is:

1. Private package registry.
2. Repository release artifacts.
3. Public package index only after the pilot succeeds and the business decision
   is made.

The current release-candidate decision boundary is summarized in
`docs/compliance/rc-readiness-summary.md` and enforced by
`scripts/check_rc_readiness.py`.

Stable promotion controls live in `docs/compliance/stable-promotion.md` and are
enforced by `scripts/check_stable_promotion.py`.

Remote CI validation evidence lives in
`docs/compliance/remote-ci-validation.md` and is enforced by
`scripts/check_remote_ci_validation.py`.

Final reviewer handoff lives in `docs/compliance/rc-review-handoff.md` and is
enforced by `scripts/check_rc_review_handoff.py`.

Staging manifest review lives in `docs/compliance/staging-manifest.md` and is
enforced by `scripts/check_staging_manifest.py`.

Private release-candidate publication controls live in
`docs/compliance/private-rc-publication.md` and are enforced by
`scripts/check_private_rc_publication.py`.

Security review scope and no-go rules live in
`docs/compliance/security-review.md` and are enforced by
`scripts/check_security_review.py`.

Real controlled pilot execution controls live in
`docs/compliance/pilot-execution.md` and are enforced by
`scripts/check_pilot_execution.py`. The guard also verifies the documented
branch, base HEAD, working-tree diff, and untracked release-asset count against
the local dirty-tree snapshot before pilot evidence can be treated as current.

Real rollback execution controls live in
`docs/compliance/rollback-execution.md` and are enforced by
`scripts/check_rollback_execution.py`. The guard also verifies the documented
branch, base HEAD, working-tree diff, and untracked release-asset count against
the local dirty-tree snapshot before rollback proof can be treated as current.

## Sprint 0: Freeze And Inventory

Goal: preserve the current work safely and make the release candidate diff
reviewable.

Actions:

- Work on branch `production-rc-0.3.0`.
- Capture a full `git status --short --branch` snapshot.
- Capture `git diff --stat` and group changes by subsystem:
  branding, workflows, docs, runtime modules, tests, scripts, packaging.
- Confirm no generated cache, local artifact, virtualenv, or accidental file is
  included in the release diff.
- Confirm all public branding names Techrevati doo only.
- Confirm `techrevati.runtime` remains unchanged.
- Run the full production gate from a clean shell.
- Record any remaining debt as explicit backlog items, not hidden review notes.
- Use `docs/compliance/final-diff-review.md` for subsystem review and keep it
  enforced by `scripts/check_final_diff_review.py`.
- Use `docs/compliance/rc-review-handoff.md` as the final reviewer handoff
  before stage, tag, or publish.
- Use `docs/compliance/staging-manifest.md` and
  `scripts/check_staging_manifest.py` to classify every untracked RC asset
  before staging.
- Use `docs/compliance/guard-calibration.md` to triage guard false positives
  before tagging or stable promotion.

Debt to pay in this sprint:

- Unreviewed large diff.
- Missing release-candidate inventory.
- Any accidental files or stale generated outputs.
- Unclassified or unexplained guard false positives.

Definition of Done:

- Branch exists.
- Diff inventory exists.
- Full gate passes.
- No unexplained tracked or untracked files remain in the release set.
- New guards have documented scope, workflow classification, and
  false-positive handling.

## Sprint 1: Public API And Compatibility Freeze

Goal: prevent accidental breaking changes before the release candidate.

Actions:

- Review all public exports from `techrevati.runtime`.
- Confirm deprecated names remain available where promised.
- Confirm public API guard expectations match the intended `0.3.0` surface.
- Freeze behavior for core primitives:
  `AgentSession`, guardrails, governance, permissions, usage tracking, retry,
  routing, checkpoints, sinks, streaming, hooks, and OTel integration.
- Freeze terminal failure-class semantics for policy, safety, governance,
  rate-limit, tool, dependency, local persistence/I/O, validation, and model
  failures.
- Add or tighten regression tests for any public behavior discovered during
  review.
- Reject new features until stable `0.3.0` is released.

Debt to pay in this sprint:

- Accidental exports.
- Weak public boundary validation.
- Missing compatibility tests.
- Unclear deprecation behavior.

Definition of Done:

- Public API guard passes.
- Deprecation tests pass.
- All public behavior changes are documented in changelog/release notes.
- Breaking changes are deferred to a later minor release.

## Sprint 2: Release Engineering For `0.3.0rc1`

Goal: make release creation repeatable and tag-gated.

Actions:

- Change version from `0.3.0.dev1` to `0.3.0rc1`.
- Add changelog entry for `0.3.0rc1`.
- Verify version consistency across package metadata, runtime `__version__`,
  changelog, and release tag.
- Build wheel and sdist.
- Run distribution checks and `twine check`.
- Run zero-dependency wheel install smoke.
- Run optional OTel extra smoke.
- Generate CycloneDX SBOM JSON and XML.
- Confirm release workflow blocks invalid tags.
- Confirm private registry publish steps are documented.

Debt to pay in this sprint:

- Manual release assumptions.
- Missing RC metadata.
- Artifact verification gaps.

Definition of Done:

- `v0.3.0rc1` can be tagged safely.
- Wheel and sdist are verified.
- SBOM is generated.
- Release notes clearly mark the build as a release candidate.

### Private RC Publication Steps

Use these steps for `0.3.0rc1`:

1. Start from the reviewed release-candidate branch.
2. Confirm `pyproject.toml`, `CHANGELOG.md`, and `docs/changelog.md` all point
   to `0.3.0rc1`.
3. Run the full production gate from a clean shell.
4. Require remote CI to pass on the release candidate commit.
5. Complete the remote CI validation checklist for the same reviewed commit.
6. Complete the guarded security review before creating the private RC tag.
7. Create tag `v0.3.0rc1` only after the local/server gate and remote CI are
   both green.
8. Let the release workflow build the wheel, source archive, and SBOM files.
9. Verify the repository release artifacts: wheel, source archive, SBOM JSON,
   and SBOM XML.
10. Verify `private-package-dist` with distribution and metadata checks before
   publishing.
11. Publish only the wheel and source archive to the private registry or
   controlled artifact channel.
12. Keep SBOM files attached as repository release evidence; do not upload them
   as package artifacts.
13. Do not publish to a public package index until the controlled RC pilot has
    passed and explicit approval is recorded.

Private registry credentials must be short-lived or managed by the CI secret
store. They must never be committed, printed in logs, or stored in release
artifacts.

The guarded private RC publication checklist in
`docs/compliance/private-rc-publication.md` must be completed before pilot use.

## Sprint 3: Security And Compliance Pass

Goal: reduce supply-chain, secret leakage, and audit risks before the pilot.

Actions:

- Run a repository secret scan.
- Run dependency vulnerability scanning for dev, docs, build, release, and OTel
  extras.
- Confirm runtime dependencies remain empty.
- Confirm workflow actions are pinned.
- Confirm workflow permissions are minimal.
- Review logs, traces, diagnostics, and error handling for raw secret leakage.
- Review guardrails, permissions, and governance defaults for pilot use.
- Confirm audit events and terminal failure classes preserve safety semantics:
  `governance_breach`, `permission_denied`, and `guardrail_violation` must not
  be collapsed into generic dependency or model failures.
- Confirm local persistence, database, disk, and filesystem failures classify
  as dependency failures rather than model failures.
- Confirm fallback caller validation failures classify as `validation_error`
  rather than model failures without masking more specific classifier matches.
- Confirm provider/model prompt or content-policy rejections classify as
  `prompt_rejection` rather than generic model failures.
- Confirm runtime rate-limiter hard-stops classify as `rate_limit` rather than
  generic model failures.
- Confirm uncaught `MaxIterationsExceededError` terminal failures classify as
  `governance_breach` rather than generic model failures.
- Confirm caller-driven cancellation classifies as `cancelled` rather than
  `unknown`.
- Confirm caller-driven cancellation does not set OTel `error.type` or
  `StatusCode.ERROR`.
- Confirm event schema validation rejects inconsistent cancellation status and
  failure-class combinations.
- Confirm `agent.failed` schema validation rejects payloads without a
  `failure_class`.
- Update `SECURITY.md` with supported versions, SBOM/provenance guidance, and
  incident reporting expectations.
- Document a lightweight threat model for agent runtime deployments.
- Keep the security review checklist explicit and pending until reviewer
  sign-off is recorded.

Debt to pay in this sprint:

- Supply-chain review gaps.
- Weak incident response documentation.
- Possible sensitive-data leakage through logs or traces.

Definition of Done:

- No unresolved high or critical security findings.
- SBOM/provenance process is documented.
- Security docs are usable by operators and downstream users.

## Sprint 4: Operations And Observability

Goal: make pilot behavior visible and diagnosable.

Required signals:

- Session count.
- Success and failure rate.
- Guardrail blocks.
- Permission denials.
- Governance breaches.
- Failure-class distribution, including `governance_breach`,
  `permission_denied`, and `guardrail_violation`.
- Retry attempts.
- Provider switches.
- Token usage.
- Estimated cost.
- Tool call count.
- Checkpoint writes and replays.
- Event and usage sink failures.
- Turn latency and tool-call latency.

Actions:

- Define a minimal OTel collector/exporter setup for pilot deployments.
- Confirm event and usage sinks are enabled in the pilot workflow.
- Define retention for durable event and usage records.
- Add an operator runbook for:
  version checks, event inspection, usage inspection, governance breaches,
  rollback, pilot shutdown, and diagnostic bundle collection.
- Define minimum alerts:
  runtime exception spike, governance terminate spike, cost threshold breach,
  provider failure spike, sink persistence failure, and OTel export failure.
- Keep the pilot operations runbook in
  `docs/compliance/pilot-operations-runbook.md` and enforce it with
  `scripts/check_operations_runbook.py`.

Debt to pay in this sprint:

- Missing runbook.
- Missing alert definitions.
- Unclear log/event retention.

Definition of Done:

- Operators know how to observe, diagnose, and roll back the pilot.
- Pilot telemetry is sufficient to evaluate success criteria.
- Rollback is tested outside the production pilot.

## Sprint 5: Controlled RC Pilot

Goal: prove `0.3.0rc1` in one real workflow before stable release.

Pilot workflow:

- One internal inbound support or back-office agent workflow.
- Limited users and limited request volume.
- No destructive tool action without explicit permission.
- Guardrails enabled before and after model/tool execution.
- Governance hard limits enabled.
- Event and usage persistence enabled.
- OTel or equivalent telemetry enabled.

Controlled test scenarios:

- Successful session.
- Prompt-injection attempt.
- Permission denial.
- Guardrail block.
- Max-iterations breach.
- Max-tool-calls breach.
- Provider failover.
- Checkpoint resume.
- Sink failure diagnostic.
- Rollback to the previous known-good version.

Evidence preparation:

- Use the pilot execution checklist at
  `docs/compliance/pilot-execution.md` to separate the local dry-run from the
  real downstream pilot. Its guard must pass with matching branch, base HEAD,
  working-tree diff, and untracked release-asset snapshot parity before launch.
- Use the rollback execution checklist at
  `docs/compliance/rollback-execution.md` to prove the previous known-good
  version can run in the pilot environment. Its guard must pass with matching
  branch, base HEAD, working-tree diff, and untracked release-asset snapshot
  parity before rollback proof is accepted.
- Run `scripts/check_pilot_dry_run.py` before the real downstream pilot to
  validate local pilot wiring.
- Use the controlled RC pilot evidence template at
  `docs/compliance/pilot-evidence-template.md` for the pilot go/no-go record.
- Use the rollback proof checklist at
  `docs/compliance/rollback-proof-checklist.md`.
- Keep both templates enforced by `scripts/check_pilot_evidence.py`.

Success criteria:

- Zero P0 incidents.
- Zero unresolved P1 incidents.
- At least 99 percent of sessions complete without runtime crash.
- Blocked, denied, and breached actions are audit-ready.
- Terminal policy and safety stops carry the expected failure classes:
  `permission_denied`, `guardrail_violation`, and `governance_breach`.
- Usage and cost tracking are reasonable and explainable.
- Recovery and provider failover behavior is visible in logs/events.
- Rollback has been tested and documented.

Debt to pay in this sprint:

- Unknown real-world behavior.
- Unproven rollback.
- Unproven observability quality.

Definition of Done:

- Pilot evidence is collected.
- Go/no-go decision is documented.
- Any bug that affects stable readiness has an owner and severity.

## Sprint 6: RC Bugfix Window

Goal: fix only what the pilot proves is necessary.

Rules:

- No new features.
- No opportunistic refactors.
- Bugfixes, docs fixes, test fixes, and security fixes only.
- Every pilot bug gets a regression test.
- Breaking changes are deferred to `0.4.0` unless required to resolve a P0.

Possible outcomes:

- Promote `0.3.0rc1` to `0.3.0` if no serious issues are found.
- Cut `0.3.0rc2` if minor fixes are needed.
- Stop stable release if an architectural issue appears.

Debt to pay in this sprint:

- Pilot-discovered defects.
- Missing regression coverage.
- Documentation mismatches.

Definition of Done:

- All stable-blocking pilot issues are closed.
- Full gate and remote CI pass.
- Release notes accurately describe RC findings and fixes.

## Sprint 7: Stable `0.3.0` Release

Goal: publish the production package.

Actions:

- Change version from final RC to `0.3.0`.
- Finalize changelog.
- Run full local/server gate.
- Require remote CI green on the release commit.
- Complete the stable promotion record with fresh external evidence.
- Tag `v0.3.0`.
- Publish to private registry.
- Create a repository release with wheel, sdist, SBOM JSON, and SBOM XML.
- Publish to a public package index only if approved after pilot.
- Record rollback target and rollback command.

Debt to pay in this sprint:

- Final release metadata gaps.
- Missing stable release evidence.

Definition of Done:

- Stable artifacts are published.
- Stable promotion evidence is archived.
- Operators know the rollback version.
- Release evidence is archived.

## Sprint 8: Post-Production Stabilization

Goal: watch the first production period closely and avoid silent drift.

Duration: 7 to 14 days after stable production use begins.

Actions:

- Review metrics daily.
- Review incidents and near misses.
- Track cost drift.
- Track provider failover rates.
- Track guardrail false positives and false negatives.
- Track release guard false positives and calibration drift.
- Track governance breach patterns.
- Track terminal failure-class distribution and investigate any unexpected
  `unknown`, dependency, or model classification for policy/safety stops.
- Track persistence, database, disk, and filesystem dependency classifications
  separately from model-provider failures during pilot review.
- Maintain patch release readiness for `0.3.1` if needed.
- Start the `0.4.0` backlog only after `0.3.0` is stable.

Debt to pay in this sprint:

- Post-release operational findings.
- Pilot assumptions that did not hold at production volume.

Definition of Done:

- No open P0 or unresolved P1 issues.
- No worsening monitoring trend.
- Patch or follow-up backlog is documented.

## Debt Payment Cadence

Debt must be paid continuously, not deferred to the end:

- Every sprint begins with a short debt scan for its subsystem.
- Any defect discovered while hardening gets a regression test.
- Any new guard or release rule gets a matching test.
- Any guard false positive gets triaged as a guard bug, repository bug, or
  documented policy exception.
- Any public behavior change gets documentation or changelog coverage.
- Any skipped item must be written as a backlog entry with a reason.
- No sprint closes with hidden, undocumented production risk.

## Production Gate Command Shape

The release candidate gate must include:

- all repository check scripts except distribution/tag-only checks when not in
  release context,
- `ruff check src/ tests/ scripts/`,
- `ruff format --check src/ tests/ scripts/`,
- `mypy src/ --strict`,
- full pytest with coverage,
- per-module coverage threshold check,
- strict docs build,
- public branding check over generated docs,
- wheel/sdist build,
- distribution artifact check,
- `twine check`,
- release-context evidence smoke with `python scripts/install_toolchain.py
  release` available,
- isolated temporary virtual environment for the release-context evidence smoke,
- forced no-index/no-dependency install from the built wheel before release
  evidence generation,
- SBOM JSON and XML generation for the built wheel,
- `SHA256SUMS` generation for wheel, source archive, SBOM JSON, and SBOM XML,
- `python scripts/check_release_evidence.py dist` over the completed evidence
  directory.

## Go/No-Go Matrix

No-go conditions:

- Any P0 pilot incident.
- Any unresolved P1 issue.
- Remote CI red on release commit.
- Public branding violation.
- Public API guard failure.
- Security high or critical finding without explicit mitigation.
- Missing rollback path.
- Missing release artifacts, SBOM, or `SHA256SUMS`.

Conditional go:

- P2 issues with documented workaround.
- Minor docs-only defects with patch planned.
- Non-critical false-positive guardrails with tuning plan.

Go:

- All gates pass.
- Pilot success criteria are met.
- Operators can monitor and roll back.
- Release artifacts are verified and archived.

## Explicit Non-Goals For `0.3.0`

- No hosted API service.
- No new auth service.
- No database migration framework beyond the existing SQLite reference stores.
- No additional required runtime dependency.
- No breaking namespace change.
- No public package index release unless approved after RC pilot.
