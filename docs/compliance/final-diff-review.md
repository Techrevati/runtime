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

Latest local/server evidence collected on 2026-06-01. The release candidate now
lives as committed history on `production-rc-0.3.0`, so the snapshot measures the
committed branch delta `main...HEAD` rather than an uncommitted working tree:

- branch: `production-rc-0.3.0`,
- tracked diff: 191 files changed, 28792 insertions, 1810 deletions,
- untracked release assets: 0 files (all release assets are committed),
- full production gate: 1,053 tests passed with 94.76 percent total coverage,
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
| Repository hygiene | No generated cache, build output, virtualenv, or accidental files | Reviewed |
| Branding and authorship | Public branding is limited to Techrevati doo | Reviewed — codex/claude vendor branding removed |
| Package metadata | Version, changelog, license, authorship, Python support, zero runtime dependencies | Reviewed |
| Workflows and automation | Minimal permissions, pinned actions, release tag gates, timeouts, guard stack parity | Reviewed |
| Guard scripts and tests | False positives, maintainability, CI parity, failure messages | Reviewed — 3 findings fixed |
| Public API compatibility | Frozen package exports, documented imports, deprecation behavior | Reviewed |
| Runtime behavior | Validation, failure handling, observability, durability, governance, guardrails, permissions | Reviewed — 5 findings fixed, 1 accepted |
| Documentation | Operator usefulness, publication safety, public branding, release boundaries | Reviewed |
| Examples and migrations | Accurate upgrade guidance and no stale forward-looking promises | Reviewed |

## Review Findings (2026-06-01)

A high-effort multi-angle review of the committed branch diff was run by
subsystem. All fixes ship with regression tests; post-fix gate is 1,053 tests.

### Fixed in this review pass

| # | Subsystem | Finding | Fix |
|---|---|---|---|
| 1 | Guard scripts | `check_secret_leaks` allow-list matched placeholder words as unanchored substrings, so a real credential merely containing `example`/`not-a-secret` was allow-listed | Anchored allow-list to distinct tokens |
| 2 | Guard scripts | `check_secret_leaks` used `\b` boundaries, missing prefixed env names like `DATABASE_PASSWORD=` | Treat alphanumerics as the name boundary |
| 3 | Guard scripts | `check_workflow_pinning` rejected a correctly SHA-pinned but YAML-quoted action | Strip surrounding quotes before the pin check |
| 4 | Runtime | `arun_turn_stream` recorded the recovery/governance failure outcome after yielding `final`, so a consumer breaking on `final` skipped it | Record before the terminal yield |
| 5 | Runtime | `pilot` ran regex patterns through the tool-name validator (case-insensitive dedup, strip) | Dedicated regex validator with compile-check |
| 8 | Runtime | `InMemorySaver.list` and `SqliteSaver.list` diverged on `before=` under equal timestamps | Aligned in-memory ordering to `(created_at, id)` |
| 7 | Runtime | otel tool spans were keyed by `(role, phase, tool)`, so two concurrent calls to the same tool collided and the second force-closed the first as `tool_span_interrupted` | Per-key LIFO span stack so concurrent same-tool calls each get their own span |
| 9 | Runtime | `decorrelated` jitter could return a delay below `base` for a tiny `prev_delay` | Clamp upper bound to `>= base` |

### Reviewed and accepted (not a defect)

- otel `_is_terminal_parent_close` keys terminality on empty event data: a
  deliberate, tested convention (a `failed` event carrying data is a warning
  child, not a session end), not a bug.

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
| Reviewer | Automated multi-angle review (Claude), pending human sign-off |
| Review date | 2026-06-01 |
| Commit SHA | tip of `production-rc-0.3.0` |
| Remote CI result | Green — 12/12 checks on `c0d8233` (re-runs per push) |
| Full gate result | 1,053 tests passed, 94.76% coverage (local) |
| Findings count | 9 (8 fixed, 1 accepted) |
| Stable blockers | Security review, private RC publication, controlled pilot, rollback proof, stable promotion |
| Decision | Pending / Approved / Changes required |

Approval means the release candidate diff is coherent enough for private RC
publication. It does not approve stable `0.3.0`; stable release still requires
controlled RC pilot evidence and rollback proof.
