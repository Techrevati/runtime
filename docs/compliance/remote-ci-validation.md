# Remote CI Validation

Author: Techrevati doo

Remote CI validation status: Pending until remote CI passes on the reviewed
commit.

This checklist records the remote CI evidence required before `0.3.0rc1` can be
tagged or used for private RC publication. It keeps the local/server gate and
remote CI gate separate so a green local run is not mistaken for release
validation.

## Purpose

Remote CI validation proves that the reviewed release-candidate commit passes
in the clean hosted workflow environment. It must run after the final diff is
ready and before the release tag is created.

## Preflight Snapshot

Latest preflight snapshot collected on 2026-06-01 before staging:

- branch: `production-rc-0.3.0`,
- base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,
- current working-tree release diff: 85 files changed, 8,859 insertions,
  1,794 deletions,
- untracked release assets: 104 files, all classified by
  `docs/compliance/staging-manifest.md`,
- local/server full production gate: 999 tests passed with 94.85 percent total
  coverage,
- local workflow readiness checks: CI guardrail guard, release workflow guard,
  workflow hardening guard, workflow action pinning guard, and remote CI
  validation guard passed,
- remote CI validation status: Pending until remote CI passes on the reviewed
  release-candidate commit.

This is preflight evidence only. The current release set is still a working
tree diff, so hosted CI cannot validate it until the reviewer-approved files
are staged and committed. Before tagging `v0.3.0rc1`, record the reviewed
commit SHA below and attach the remote workflow run URL for that exact commit.

## Commit Parity

Remote CI evidence is valid only when all of these match:

- reviewed commit SHA,
- release-candidate branch,
- package version `0.3.0rc1`,
- changelog entry for `0.3.0rc1`,
- local/server full production gate evidence,
- remote CI workflow run commit.

If the branch changes after remote CI passes, the remote CI evidence is stale
and must be collected again.

## Required Jobs

The remote CI run must include:

- test matrix for Python 3.11, 3.12, and 3.13,
- build matrix for Python 3.11, 3.12, and 3.13,
- zero-dependency wheel smoke for Python 3.11, 3.12, and 3.13,
- lint, format, strict typing, and full tests,
- total and per-module coverage gates,
- all verify-time guard scripts,
- distribution artifact checks,
- package metadata checks,
- no-dependency wheel install smoke,
- documentation build and public branding checks.

The release verification workflow must run the same verify-time guard stack
before any package publish step.

## Evidence To Capture

Record these fields before creating `v0.3.0rc1`:

- workflow name,
- run URL or immutable run identifier,
- commit SHA,
- branch name,
- run start and finish time,
- final result,
- Python matrix results,
- build artifact check result,
- zero-dependency smoke result,
- documentation build result,
- public branding result,
- failed job logs when any job is red,
- reviewer who compared the run to the local/server gate.

## Failure Triage

Every red remote CI result must be classified as one of:

- product defect,
- test defect,
- workflow defect,
- environment defect,
- dependency or toolchain outage.

Do not tag, publish, or pilot from a commit with a red or unexplained remote CI
run. If the failure is an environment defect or toolchain outage, rerun CI and
record the passing run before continuing.

## No-Go Rules

Do not tag, publish, or promote when any of these are true:

- remote CI is missing,
- remote CI ran on a different commit,
- any required matrix job is missing,
- any required job is red,
- failed logs have not been triaged,
- local/server gate and remote CI use different package versions,
- release verification does not include the verify-time guard stack,
- documentation or public branding checks are absent from release evidence.

## Sign-Off Template

Use this template for the remote CI validation record:

| Field | Value |
|---|---|
| Reviewer | Pending |
| Commit SHA | Pending |
| Branch | `production-rc-0.3.0` |
| Workflow name | Pending |
| Run URL or identifier | Pending |
| Result | Pending |
| Python matrix complete | Pending |
| Build matrix complete | Pending |
| Zero-dependency smoke complete | Pending |
| Docs and branding complete | Pending |
| Failed jobs triaged | Pending |
| Decision | Pending / Approved / Changes required |
