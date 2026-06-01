# Guard Calibration

Author: Techrevati doo

Guard calibration status: Pending until remote CI false-positive review is
complete.

This checklist keeps the release-candidate guard stack strict without making a
stable release depend on unexplained false positives. It does not replace the
full production gate, remote CI, final diff review, controlled pilot, or
rollback proof.

## Purpose

The release candidate now has many small guard scripts. Each guard is useful
only if it is consistently wired, reviewed for false positives, and kept honest
about the risk it owns.

Guard calibration has three goals:

- every `scripts/check_*.py` file is visible in this inventory,
- every verify-time guard runs in both CI and release verification where
  applicable,
- every false positive is triaged before stable release instead of hidden by
  weakening a guard.

## Guard Inventory

| Guard | Scope | False-positive review | Status |
|---|---|---|---|
| `check_changelog.py` | Release notes and version entry policy | Confirm date/version wording before RC and stable tags | Pending remote CI |
| `check_ci_guardrails.py` | Workflow guard classification and CI parity | Confirm new guards are either verify-time or explicitly special-case | Pending remote CI |
| `check_dependency_vulnerabilities.py` | Dev, docs, build, release, and OTel dependency audits | Confirm scanner findings are real, fixed, or explicitly downgraded with evidence | Pending remote CI |
| `check_distribution.py` | Wheel and source distribution contents | Confirm artifact failures are package defects, not local build leftovers | Pending remote CI |
| `check_docs_public_api.py` | Documented package-level imports | Confirm docs mirror the frozen public API without accidental examples | Pending remote CI |
| `check_docs_publication.py` | Strict docs publication contract | Confirm docs nav, theme, and branding failures are real publication risks | Pending remote CI |
| `check_final_diff_review.py` | Final RC diff review completeness | Confirm review remains pending until a reviewer signs off | Pending remote CI |
| `check_guard_calibration.py` | Guard inventory, false-positive process, and workflow wiring | Confirm this inventory matches the actual guard stack | Pending remote CI |
| `check_maintenance.py` | Maintenance automation and stale policy checks | Confirm schedule and ownership checks match current maintenance practice | Pending remote CI |
| `check_module_coverage.py` | Per-module coverage floor | Confirm uncovered lines are real runtime risk, not generated or impossible code | Pending remote CI |
| `check_operations_runbook.py` | Pilot operations runbook completeness | Confirm alert, rollback, shutdown, and diagnostic guidance is actionable | Pending remote CI |
| `check_package_policy.py` | Package metadata and runtime dependency policy | Confirm package metadata failures are release blockers | Pending remote CI |
| `check_pilot_dry_run.py` | Local controlled scenario dry-run wiring | Confirm dry-run failures map to pilot setup defects | Pending remote CI |
| `check_pilot_evidence.py` | Pilot evidence and rollback proof templates | Confirm template failures are real missing go/no-go evidence | Pending remote CI |
| `check_pilot_execution.py` | Real controlled pilot execution boundary | Confirm local dry-run output cannot replace downstream pilot evidence | Pending remote CI |
| `check_precommit_config.py` | Developer pre-commit parity | Confirm local hooks match CI-required checks | Pending remote CI |
| `check_private_rc_publication.py` | Private RC publication controls | Confirm release workflow cannot fall back to public package publication | Pending remote CI |
| `check_public_api.py` | Frozen package public API | Confirm API changes are intentional and documented before any update | Pending remote CI |
| `check_public_branding.py` | Public branding restriction to Techrevati doo | No bypass; public branding violations must be fixed | Pending remote CI |
| `check_python_support.py` | Supported Python version policy | Confirm support matrix matches CI and package metadata | Pending remote CI |
| `check_rc_readiness.py` | RC and stable release decision boundaries | Confirm stable blockers stay explicit until real evidence exists | Pending remote CI |
| `check_rc_review_handoff.py` | Final reviewer handoff packet | Confirm review evidence, blockers, and no-go rules stay in one pending checklist | Pending remote CI |
| `check_remote_ci_validation.py` | Remote CI commit-parity and evidence checklist | Confirm local/server gate is not mistaken for remote CI evidence | Pending remote CI |
| `check_release_evidence.py` | Release evidence artifact metadata, SBOM format, and checksum verification | Confirm wheel, sdist, SBOM, and checksum evidence remain complete, version-matched, parsable, and untampered | Pending remote CI |
| `check_release_tag.py` | Release tag and package version consistency | Confirm tag failures block publication | Pending remote CI |
| `check_release_workflow.py` | Release workflow safety | Confirm publish, artifact, SBOM, and tag checks are enforced | Pending remote CI |
| `check_rollback_execution.py` | Real rollback proof boundary | Confirm command-shape checks cannot replace downstream rollback evidence | Pending remote CI |
| `check_stable_promotion.py` | Stable promotion evidence boundary | Confirm stable release cannot bypass external evidence approval | Pending remote CI |
| `check_staging_manifest.py` | Staging manifest and untracked release-asset classification | Confirm all untracked RC assets are intentional and generated artifacts stay excluded | Pending remote CI |
| `check_repo_hygiene.py` | Repository cleanliness and ignored artifacts | Confirm generated files or caches are not entering the release diff | Pending remote CI |
| `check_secret_leaks.py` | Repository-wide secret leakage patterns | No bypass; possible secrets must be rotated or proven benign | Pending remote CI |
| `check_security_patterns.py` | Source, script, and test security-sensitive coding patterns; literal truthy subprocess shell bypasses; missing subprocess timeouts; implicit `subprocess.run` check semantics; direct `subprocess.Popen`; missing HTTP client timeouts; disabled TLS verification; raw logging tracebacks; raw exception text in logging/observability payloads; unsafe YAML loading | Confirm failures are fixed or documented as intentional safe patterns | Pending remote CI |
| `check_security_review.py` | Security review evidence and no-go rules | Confirm reviewer sign-off stays pending until actual review evidence exists | Pending remote CI |
| `check_source_hygiene.py` | Source, script, and test hygiene; stale markers; CLI-script debug leftovers; generated/cache directory skips; `NotImplementedError` runtime stubs; implicit text-file encodings | Confirm failures are real maintainability risk or documented exceptions | Pending remote CI |
| `check_toolchain_pins.py` | Toolchain pinning for build, docs, audit, and release | Confirm pins are current and deterministic | Pending remote CI |
| `check_version_consistency.py` | Package, runtime, docs, and changelog version consistency | Confirm all version surfaces agree before tagging | Pending remote CI |
| `check_workflow_hardening.py` | Workflow permissions, credentials, and shell hardening | Confirm failures are release blockers unless workflow is removed | Pending remote CI |
| `check_workflow_pinning.py` | Workflow action SHA pinning | No bypass; unpinned actions must be pinned before release | Pending remote CI |

## False-Positive Handling

Do not weaken a guard only to make CI green.

Every apparent false positive must be classified as one of:

- bug in guard,
- bug in repository,
- intentional policy exception.

Policy exceptions require a written reason in the changelog, compliance docs, or
release-candidate inventory before the exception is accepted. Security,
branding, public API, release workflow, and workflow pinning guards are
high-risk controls and must not be bypassed to publish a release candidate.

## CI Parity

Every verify-time guard must run in both the CI test job and the release
verification job. Guards that only make sense in a special context, such as
distribution artifacts, release tags, or coverage reports, must be explicitly
classified outside the verify-time guard list.

`scripts/check_ci_guardrails.py` enforces the verify-time and special-case
classification. `scripts/check_guard_calibration.py` enforces this calibration
document, the documentation nav entry, and CI/release workflow wiring.

## Calibration Procedure

Run this procedure before tagging `v0.3.0rc1` and again before promoting
stable `0.3.0`:

1. Run the full local/server production gate.
2. Run remote CI on the exact release candidate commit.
3. For each failing guard, classify the failure as guard bug, repository bug,
   or intentional policy exception.
4. Fix guard bugs with focused tests.
5. Fix repository bugs in the owning subsystem with regression coverage.
6. Record any accepted policy exception in release evidence before continuing.
7. Re-run the focused guard, the affected tests, and the full gate.
8. Keep this document pending until remote CI has been reviewed for false
   positives.

## No-Go Rules

Do not tag, publish, or promote the release when any of these are true:

- a guard failure is dismissed as a false positive without written triage,
- a new guard is skipped without owner, scope, and workflow classification,
- a guard passes locally but fails remote CI without triage,
- a high or critical dependency finding is unresolved,
- a possible secret leak is unresolved,
- public branding contains anything other than Techrevati doo,
- the frozen public API changes without documented approval,
- a release or workflow pinning guard is bypassed.

## Sign-Off Template

Use this template during release-candidate review:

| Field | Value |
|---|---|
| Reviewer | Pending |
| Remote CI run | Pending |
| False positives found | Pending |
| Accepted exceptions | Pending |
| Fixed guard bugs | Pending |
| Fixed repository bugs | Pending |
| Decision | Pending / Approved / Changes required |
