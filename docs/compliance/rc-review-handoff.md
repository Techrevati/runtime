# RC Review Handoff

Author: Techrevati doo

RC review handoff status: Pending until final reviewer signs off.

This handoff is the final release-candidate review packet for `0.3.0rc1`. It
does not approve the release candidate, publish artifacts, or mark stable
production readiness complete. It gives the reviewer one place to confirm the
diff scope, gate evidence, external blockers, and no-go rules before any stage,
tag, or publish action.

## Purpose

The release-candidate diff is large enough that review must be evidence-led.
This handoff ties together the final diff review, release-candidate inventory,
staging manifest, remote CI validation, security review, private RC
publication, controlled pilot evidence, and rollback proof.

## Package Snapshot

Review the release candidate as this package shape:

- package name: `techrevati-runtime`,
- technical namespace: `techrevati.runtime`,
- release candidate: `0.3.0rc1`,
- target stable release: `0.3.0`,
- release shape: Python runtime package,
- first production channel: private package registry or controlled internal
  artifact channel,
- hosted service wrapper: out of scope for `0.3.0`,
- public package index publication: out of scope until controlled pilot
  approval.

## Review Inputs

The reviewer must inspect or attach these inputs:

- `docs/compliance/rc-inventory.md`,
- `docs/compliance/final-diff-review.md`,
- `docs/compliance/staging-manifest.md`,
- `docs/compliance/remote-ci-validation.md`,
- `docs/compliance/security-review.md`,
- `docs/compliance/private-rc-publication.md`,
- `docs/compliance/pilot-evidence-template.md`,
- `docs/compliance/rollback-proof-checklist.md`,
- `git status --short --branch`,
- `git ls-files --others --exclude-standard`,
- `git diff --stat`,
- full production gate output,
- remote CI result for the same reviewed commit.

## Diff Review Checklist

Confirm these release-candidate groups before approving private RC publication:

- repository hygiene and ignored artifacts,
- untracked release assets classified by the staging manifest,
- public branding limited to Techrevati doo,
- package metadata, changelog, and version consistency,
- workflow hardening, action pinning, and release tag gates,
- guard scripts, guard tests, and CI parity,
- public API compatibility and documented import surface,
- runtime behavior by subsystem,
- documentation accuracy and operator readiness,
- examples, tutorials, and migration guidance.

## Gate Evidence Summary

The handoff must point to the latest current gate evidence in
`docs/compliance/rc-inventory.md`. The evidence must include:

- full production gate result,
- total test count,
- total coverage,
- per-module coverage floor result,
- strict typing result,
- lint and format result,
- strict documentation build result,
- public branding guard result,
- distribution artifact check result,
- package metadata rendering result,
- all verify-time guard results,
- staging manifest guard result.

Current handoff snapshot collected on 2026-06-01:

- branch: `production-rc-0.3.0`,
- tracked diff: 191 files changed, 28792 insertions, 1810 deletions (committed
  branch delta `main...HEAD`; the release candidate is no longer an uncommitted
  working tree),
- untracked release assets: 0 files, all release assets are committed,
- latest local/server full production gate: 1,053 tests passed with 94.76
  percent total coverage,
- local/server documentation, public branding, source hygiene, security
  pattern, release, package, workflow, and distribution guards: passed,
- remote CI result: pending,
- security review result: pending,
- private RC publication readiness: pending,
- stable release blockers: final diff sign-off, remote CI parity, security
  sign-off, private RC publication, controlled pilot, and rollback proof.

## External Blockers

Keep these blockers open until real evidence exists:

| Blocker | Required evidence |
|---|---|
| Final diff review | Reviewer sign-off for all review groups |
| Remote CI validation | Green remote CI and completed remote CI validation checklist on the same reviewed commit |
| Security review | Completed security review checklist with no unresolved high or critical findings |
| Private RC publication | Published private wheel/source archive and repository release evidence |
| Controlled RC pilot | Completed controlled pilot evidence template |
| Rollback proof | Completed rollback proof checklist in the pilot environment |

## No-Go Rules

Do not stage, tag, publish, pilot, or promote when any of these are true:

- reviewer handoff is unsigned,
- final diff review is unsigned,
- untracked release assets are not classified by the staging manifest,
- remote CI is missing, red, or for a different commit,
- security review is unsigned,
- private RC publication checklist is incomplete,
- public branding guard fails,
- secret leak guard fails,
- dependency vulnerability guard has unresolved high or critical findings,
- workflow hardening or action pinning guard fails,
- package-level public API differs from the frozen set,
- `techrevati.runtime` namespace changes,
- release artifacts or SBOM files are missing,
- controlled pilot evidence is marked complete before the real pilot,
- rollback proof is marked complete before the real pilot environment test.

## Sign-Off Template

Use this template for the reviewer handoff record:

| Field | Value |
|---|---|
| Reviewer | Pending |
| Review date | Pending |
| Commit SHA | Pending |
| Branch | `production-rc-0.3.0` |
| Full gate result | Pending |
| Remote CI result | Pending |
| Security review result | Pending |
| Private RC publication readiness | Pending |
| Remaining stable blockers | Pending |
| Accepted RC risks | Pending |
| Decision | Pending / Approved / Changes required |
