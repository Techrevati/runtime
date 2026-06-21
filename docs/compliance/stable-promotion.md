# Stable Promotion Checklist

Author: Techrevati doo

Stable promotion status: Blocked until external evidence is complete.

This checklist controls promotion from `0.3.0rc1` to stable `0.3.0`. It is the
last decision record before a production package is published as stable.

## Purpose

Stable promotion proves that the release candidate has moved beyond local,
server, and documentation readiness into real release evidence. Do not promote
`0.3.0rc1` to stable `0.3.0` until the same commit has complete remote CI,
private publication, pilot, rollback, and reviewer evidence.

## Stable Promotion Preflight Snapshot

Latest stable promotion preflight snapshot collected on 2026-06-01:

- branch: `production-rc-0.3.0`,
- base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,
- current package version: `0.3.0rc1`,
- target stable version: `0.3.0`,
- current working-tree release diff: 85 files changed, 8,859 insertions,
  1,794 deletions,
- untracked release assets: 104 files, all classified by
  `docs/compliance/staging-manifest.md`,
- local/server full production gate: 999 tests passed with 94.85 percent total
  coverage,
- final diff review, staging manifest, and RC review handoff snapshot parity:
  passed locally/server-side,
- security, private RC publication, pilot execution, and rollback execution
  preflight snapshots: present and guarded,
- release artifact preflight: wheel, source archive, SBOM JSON, SBOM XML, and
  `SHA256SUMS` evidence passed locally/server-side,
- stable promotion guard: passed for the RC version while status remains
  blocked,
- stable `0.3.0` promotion: Blocked until final reviewer sign-off, same-commit
  remote CI, approved security review, private RC publication, controlled pilot
  evidence, rollback execution proof, zero P0 incidents, and zero unresolved P1
  incidents are complete.

This is preflight evidence only. It confirms that the local promotion controls
are ready, but it is not approval to publish or label `0.3.0` as stable.

## Promotion Boundary

The stable promotion record is separate from the release candidate inventory.
The inventory can prove that the candidate is reviewable. This checklist proves
that the reviewed candidate is safe enough to become the stable production
package.

> **Pre-1.0 policy.** This external-evidence promotion record applies to the
> `0.3.0` hardening line and to `1.0.0`+ stable promotions. Pre-1.0 (`0.x`)
> minor releases ship on the automated CI gates (lint, types, tests, coverage,
> supply-chain, public-API and branding guards); the controlled pilot, rollback
> proof, and reviewer sign-off become mandatory from `1.0.0`, when the
> API-stability contract begins.

Do not use any of these as stable approval by themselves:

- local/server production gate output,
- local pilot dry-run output,
- package build success,
- command-shape rollback proof,
- pending reviewer checklists,
- green CI from a different commit.

## Required Evidence

Stable promotion requires all of this evidence:

- final diff review checklist approved,
- RC reviewer handoff approved,
- remote CI validation checklist approved on the same commit,
- security review approved with no unresolved high or critical findings,
- private RC publication evidence captured,
- controlled RC pilot evidence template completed,
- rollback execution checklist completed in the pilot environment,
- rollback proof checklist completed in the pilot environment,
- full production gate output attached,
- wheel, sdist, SBOM JSON, SBOM XML, and SHA256SUMS evidence attached,
- public branding guard passed,
- secret leak guard passed,
- dependency vulnerability guard passed,
- zero P0 incidents,
- zero unresolved P1 incidents,
- go/no-go decision recorded.

## Evidence Freshness

Evidence is fresh only when these fields match:

- reviewed commit SHA,
- remote CI workflow commit,
- private RC package version,
- release artifact checksums,
- pilot package version,
- rollback current version,
- final changelog version.

If the branch changes after any approval, repeat the affected evidence step.
Stale remote CI, stale artifacts, or stale pilot evidence make stable promotion
no-go.

## Stable Go Conditions

Stable `0.3.0` can be promoted only when:

- all required evidence is approved,
- all stable-blocking incidents are closed,
- the rollback target is known and reachable from the controlled artifact
  channel,
- operators know the rollback command and previous known-good version,
- release notes describe the RC pilot outcome,
- the stable tag points at the same reviewed commit,
- public package index publication remains out of scope until pilot approval
  and business approval are both recorded.

The release workflow runs the stable promotion guard during verification. RC
versions may keep this checklist blocked while pilot evidence is pending. A
stable `X.Y.Z` version cannot pass the guard until the promotion status and
approval record are changed from pending to approved with real evidence.

## Stable No-Go Rules

Do not promote stable `0.3.0` when any of these are true:

- stable promotion status is pending,
- remote CI validation is missing or stale,
- private RC publication evidence is missing,
- security review is not approved,
- controlled RC pilot evidence is incomplete,
- rollback execution or rollback proof is incomplete,
- any P0 incident occurred during the pilot,
- any P1 incident remains unresolved,
- dependency vulnerability guard has unresolved high or critical findings,
- public branding guard fails,
- secret leak guard fails,
- public package index publication is attempted before approval,
- final changelog and release notes do not match the promoted package.

## Approval Record Template

Use this template for the stable promotion decision:

| Field | Value |
|---|---|
| Promotion owner | Pending |
| Reviewed commit SHA | Pending |
| Package version | `0.3.0` |
| RC source version | `0.3.0rc1` |
| Remote CI validation | Pending |
| Security review | Pending |
| Private RC publication evidence | Pending |
| Controlled RC pilot evidence | Pending |
| Rollback execution proof | Pending |
| Rollback target | Pending |
| P0 incidents | Pending |
| Unresolved P1 incidents | Pending |
| Artifact checksums | Pending |
| Public package index decision | Pending / Out of scope / Approved |
| Decision | Pending / Approved / Changes required |

## Post-Promotion Controls

After stable promotion:

- archive the stable promotion record with release evidence,
- keep rollback artifacts reachable for the stabilization window,
- monitor the first 7 to 14 days of production use,
- open a patch release issue for every stable-blocking defect,
- keep public branding, secret leak, dependency vulnerability, and remote CI
  guards enabled for patch releases.
