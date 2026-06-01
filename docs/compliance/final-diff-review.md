# Final Diff Review Checklist

Author: Techrevati doo

This checklist is the review contract for the `0.3.0rc1` release-candidate
diff. It does not mark the review complete.

Final diff review status: Pending until reviewer signs off.

Use `docs/compliance/rc-review-handoff.md` as the reviewer handoff packet that
links this checklist to gate evidence and remaining release blockers.
Use `docs/compliance/staging-manifest.md` to classify untracked release assets
before any stage, tag, or publish step.

## Review Purpose

The release candidate is a large hardening diff. Review it by subsystem instead
of reading it as one undifferentiated patch. Every finding must be fixed,
documented as a stable-release blocker, or explicitly accepted as non-blocking
release-candidate risk.

## Review Scope

Review these change groups:

- repository hygiene and generated-file exclusion,
- branding and authorship,
- package metadata, changelog, and release notes,
- workflow security and release automation,
- guard scripts and guard tests,
- runtime public API compatibility,
- runtime behavior by subsystem,
- documentation accuracy and operator readiness,
- examples and migration guidance.

Do not include ignored local artifacts such as `.venv`, `dist`, `__pycache__`,
tool caches, or local temporary evidence bundles.
Confirm `git ls-files --others --exclude-standard` contains only release assets
allowed by the staging manifest before staging.

## Review Order

1. Repository hygiene, generated files, docs theme, and ignored artifacts.
2. Branding and authorship.
3. Package metadata, changelog, version consistency, and release notes.
4. Workflow security and release automation.
5. Guard scripts and tests.
6. Runtime public API and compatibility.
7. Runtime hardening behavior by subsystem.
8. Documentation accuracy and operator readiness.
9. Full production gate evidence.

## Current Pre-Review Evidence Snapshot

Latest local/server pre-review evidence collected on 2026-06-01:

- branch: `production-rc-0.3.0`,
- tracked diff: 85 files changed, 8,859 insertions, 1,794 deletions,
- untracked release assets: 104 files,
- full production gate: 999 tests passed with 94.85 percent total coverage,
- per-module coverage floor: passed at 85 percent,
- strict typing, lint, format, strict docs build, public branding, source
  hygiene, security pattern, package, release, workflow, and distribution
  guards: passed locally/server-side.

Pre-review conclusion:

- repository shape is coherent enough for subsystem review,
- generated/cache artifacts remain excluded by repository hygiene and source
  hygiene guards,
- final approval is still pending because reviewer sign-off, remote CI parity,
  security sign-off, private RC publication, controlled pilot, and rollback
  proof are not yet complete.

## Subsystem Review Matrix

| Subsystem | Review focus | Status |
|---|---|---|
| Repository hygiene | No generated cache, build output, virtualenv, or accidental files | Pending |
| Branding and authorship | Public branding is limited to Techrevati doo | Pending |
| Package metadata | Version, changelog, license, authorship, Python support, zero runtime dependencies | Pending |
| Workflows and automation | Minimal permissions, pinned actions, release tag gates, timeouts, guard stack parity | Pending |
| Guard scripts and tests | False positives, maintainability, CI parity, failure messages | Pending |
| Public API compatibility | Frozen package exports, documented imports, deprecation behavior | Pending |
| Runtime behavior | Validation, failure handling, observability, durability, governance, guardrails, permissions | Pending |
| Documentation | Operator usefulness, publication safety, public branding, release boundaries | Pending |
| Examples and migrations | Accurate upgrade guidance and no stale forward-looking promises | Pending |

## Mandatory No-Go Rules

Do not approve the release candidate when any of these are true:

- public branding contains anything other than Techrevati doo,
- the technical namespace `techrevati.runtime` is changed,
- package-level public API exports differ from the frozen set,
- documented public imports disagree with the frozen public API,
- any workflow action is unpinned,
- workflow permissions are broader than required,
- any guard script is not classified for CI coverage,
- secret leak guard fails,
- dependency vulnerability guard has unresolved high or critical findings,
- public package index publication is enabled before pilot approval,
- full production gate fails,
- generated artifacts or local caches are included in the release diff,
- staging manifest has unclassified untracked files,
- pilot evidence or rollback proof is marked complete before the real pilot,
- remote CI is missing when tagging or publishing.

## Evidence To Attach

Attach or link this evidence before final sign-off:

- `git status --short --branch`,
- `git ls-files --others --exclude-standard`,
- `git diff --stat`,
- full production gate output,
- remote CI result for the same commit,
- wheel and sdist verification,
- SBOM JSON and XML verification,
- public branding guard output,
- secret leak guard output,
- dependency vulnerability guard output,
- RC readiness summary,
- reviewer handoff checklist,
- staging manifest checklist,
- pilot dry-run output,
- open stable-release blocker list.

## Reviewer Sign-Off Template

| Field | Value |
|---|---|
| Reviewer | |
| Review date | |
| Commit SHA | |
| Remote CI result | |
| Full gate result | |
| Findings count | |
| Stable blockers | |
| Decision | Pending / Approved / Changes required |

Approval means the release candidate diff is coherent enough for private RC
publication. It does not approve stable `0.3.0`; stable release still requires
controlled RC pilot evidence and rollback proof.
