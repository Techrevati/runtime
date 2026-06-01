# Release Candidate Readiness Summary

Author: Techrevati doo

This page is the decision boundary for `0.3.0rc1`. It separates the current
release-candidate readiness from stable production readiness.

## Current Status

`0.3.0rc1` is locally and server-gate ready for release-candidate review. It is
not stable production-ready until the remaining external evidence is collected:
final diff review, remote CI, security review, private RC publication,
controlled RC pilot evidence, and rollback proof.

The current local/server gate evidence is recorded in the release candidate
inventory. A green local/server gate is necessary but not sufficient for stable
`0.3.0`.

Current practical assessment:

- local/server technical readiness: 91 / 100,
- private RC readiness: 86 / 100,
- stable production readiness: 78 / 100.

The remaining gap is mostly external proof, not local code confidence. The next
work should prioritize final diff review, remote CI parity, security sign-off,
private RC publication, controlled pilot evidence, and rollback proof before
more broad local hardening.

## Release Decision Boundaries

Private RC publication can proceed only after:

- final diff review is complete,
- reviewer handoff is complete,
- local/server production gate is green,
- remote CI is green on the same commit,
- remote CI validation checklist is complete for the same reviewed commit,
- security review checklist is complete and signed off before private RC
  publication,
- release artifacts and SBOM files are verified,
- private registry or controlled artifact channel is ready,
- private RC publication checklist is complete,
- public package index publication is explicitly out of scope.

Stable `0.3.0` can proceed only after:

- the controlled RC pilot is complete,
- the pilot execution checklist is complete,
- rollback proof is complete in the pilot environment,
- rollback execution checklist is complete in the pilot environment,
- there are zero P0 incidents,
- there are zero unresolved P1 incidents,
- pilot usage and cost evidence is explainable,
- pilot telemetry and durable evidence are complete,
- completed pilot evidence template is attached,
- completed rollback proof checklist is attached,
- stable promotion record is approved,
- the go/no-go decision is recorded.

## Private RC Go Conditions

Before tagging `v0.3.0rc1`, verify:

- `pyproject.toml`, `CHANGELOG.md`, and `docs/changelog.md` all reference
  `0.3.0rc1`,
- local/server full production gate passes,
- remote CI passes on the reviewed commit,
- security review is approved with no unresolved high or critical findings,
- wheel and sdist pass distribution checks and `twine check`,
- SBOM JSON and XML are generated,
- runtime audit taxonomy checks pass for governance breaches, permission
  denials, guardrail violations, rate-limit stops, validation failures,
  prompt rejections, max-iterations stops, and cancellations,
- private publish credentials are managed by the CI secret store,
- private package repository URL, username, and password/token are present in
  the CI secret store,
- no token, password, or registry credential is committed or printed.

## Stable No-Go Conditions

Do not promote to stable `0.3.0` when any of these are true:

- any P0 incident occurred during the pilot,
- any P1 incident remains unresolved,
- remote CI has not validated the release commit,
- controlled RC pilot evidence is incomplete,
- rollback proof is incomplete,
- public branding guard fails,
- secret leak guard fails,
- dependency vulnerability guard has unresolved high or critical findings,
- public package index publication is being attempted before pilot approval.

## Remaining Blockers

Current stable-release blockers:

| Blocker | Status | Required evidence |
|---|---|---|
| Final diff review | Open | Reviewed release-candidate diff by subsystem and completed reviewer handoff |
| Remote CI validation | Open | Green CI and completed remote CI validation checklist on the same reviewed commit |
| Security review | Open | Completed security review checklist with no unresolved high or critical findings |
| Private RC publication | Open | Published private wheel/sdist and repository release evidence |
| Controlled RC pilot | Open | Completed pilot execution checklist and pilot evidence template |
| Rollback proof | Open | Completed rollback execution checklist and rollback proof checklist |
| Stable promotion | Open | Approved stable promotion record with fresh external evidence |

These blockers are not defects in the package. They are external production
evidence gates that must be closed before stable release.

## Evidence Checklist

Stable release evidence must include:

- full production gate output,
- remote CI status,
- remote CI validation checklist,
- security review checklist,
- release artifact verification,
- SBOM JSON and XML,
- private RC publication evidence,
- pilot operations evidence,
- terminal failure-class evidence for policy, safety, quota, validation,
  prompt rejection, cancellation, and runtime hard-stop outcomes,
- pilot execution checklist,
- controlled RC pilot evidence,
- rollback execution checklist,
- rollback proof checklist,
- stable promotion record,
- go/no-go decision,
- reviewer handoff checklist,
- final changelog and release notes.
