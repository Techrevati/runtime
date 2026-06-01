# Security Review

Author: Techrevati doo

Security review status: Pending until reviewer signs off.

This checklist records the security review required before `0.3.0rc1` private
publication, before controlled pilot use, and before stable `0.3.0` promotion.
It complements the automated security guards; it does not replace them.

## Purpose

The runtime is a library, not a service. It runs as an in-process dependency
with the caller's privileges. Security review must therefore focus on
boundaries, defaults, release artifacts, and pilot controls rather than hosted
service controls.

## Security Preflight Snapshot

Latest security preflight snapshot collected on 2026-06-01 before reviewer
sign-off:

- branch: `production-rc-0.3.0`,
- base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,
- current working-tree release diff: 85 files changed, 8,859 insertions,
  1,794 deletions,
- untracked release assets: 104 files, all classified by
  `docs/compliance/staging-manifest.md`,
- local/server full production gate: 999 tests passed with 94.85 percent total
  coverage,
- secret leak guard: passed,
- dependency vulnerability guard: passed for dev, docs, build, release, and
  OTel dependency groups,
- security pattern guard: passed for source, release scripts, and tests,
- workflow action pinning guard: passed,
- workflow hardening guard: passed,
- release workflow guard: passed,
- private RC publication guard: passed,
- public branding guard: passed,
- fresh wheel and source archive build: passed,
- distribution artifact check: passed,
- package metadata rendering with `twine check`: passed,
- local SBOM JSON, SBOM XML, and `SHA256SUMS` generation: passed,
- release evidence guard against local `dist`: passed,
- remote CI and security reviewer sign-off: Pending until the reviewed
  release-candidate commit is available and approved.

This is preflight evidence only. It helps the reviewer confirm the current
security posture, but it does not approve private publication, controlled pilot
use, or stable promotion without remote CI, human security review, and the
release workflow evidence for the same reviewed commit.

## Review Scope

Review these areas before private RC publication and pilot use:

- public API compatibility and unsafe accidental exports,
- permission enforcement and guardrail boundaries,
- governance limits and usage limits,
- terminal failure taxonomy for policy, safety, prompt rejection, validation,
  runtime rate-limit, and persistence stops,
- logs, diagnostics, events, usage records, and telemetry,
- durable sinks and local persistence,
- release artifacts, SBOM files, and private package publication,
- workflow permissions, action pinning, and release gates,
- dependency vulnerability and secret leakage results,
- pilot configuration, rollback path, and incident response expectations.

## Required Evidence

Attach or reference all of this evidence:

- full local/server production gate output,
- remote CI result for the same commit,
- secret leak guard output,
- dependency vulnerability guard output,
- security pattern guard output for source, release scripts, and tests,
  including literal truthy subprocess shell bypasses, missing subprocess
  timeouts, implicit `subprocess.run` check semantics, direct
  `subprocess.Popen`, missing HTTP client timeouts, disabled TLS verification,
  raw logging traceback, raw exception text in logging/observability payloads,
  and unsafe YAML loading checks,
- terminal failure-class regression output,
- runtime rate-limit failure-class regression output,
- max-iterations failure-class regression output,
- cancellation failure-class regression output,
- dependency I/O failure-class regression output,
- prompt rejection failure-class regression output,
- validation failure-class regression output,
- workflow action pinning guard output,
- workflow hardening guard output,
- release workflow guard output,
- private RC publication guard output,
- public branding guard output,
- distribution artifact check output,
- `twine check` output,
- SBOM JSON and XML verification,
- controlled pilot profile review,
- rollback proof checklist status.

## Runtime Risk Review

Confirm these runtime-specific boundaries:

- model output is untrusted,
- tool input derived from model output is untrusted until approved,
- tool implementations run with caller process privileges,
- `PermissionEnforcer` is a policy gate, not a sandbox,
- guardrails reduce risk but do not isolate tool bodies,
- governance limits stop sessions but do not contain already-running tool code,
- governance, permission, and guardrail stops use dedicated terminal failure
  classes rather than dependency or model failure classes,
- controlled pilot permissions deny unknown roles by default while the base
  permission policy keeps compatibility behavior unless callers opt in,
- local persistence, database, disk, and filesystem failures classify as
  dependency failures rather than model failure classes,
- fallback caller validation failures classify as validation failures rather
  than model failure classes,
- provider or model prompt/content-policy rejections classify as prompt
  rejections rather than model failure classes,
- runtime rate-limiter hard-stops classify as rate-limit failures rather than
  model failure classes,
- uncaught max-iterations hard-stops classify as governance breaches rather
  than model failure classes,
- caller-driven cancellation classifies as cancellation rather than unknown
  failure,
- caller-driven cancellation remains visible in telemetry without being marked
  as an OTel error,
- event schema validation rejects inconsistent cancellation status and
  failure-class payloads,
- event schema validation rejects `agent.failed` payloads without a
  `failure_class`,
- usage limits depend on caller-provided usage snapshots,
- destructive or high-risk tools require process, network, or OS-level
  isolation outside the runtime.

## Supply Chain Review

Confirm these supply-chain controls:

- runtime required dependency set remains empty,
- dev, docs, build, release, and OTel dependency audits have no unresolved high
  or critical findings,
- security pattern guard rejects literal truthy subprocess shell bypasses,
  missing subprocess timeouts, implicit `subprocess.run` check semantics,
  direct `subprocess.Popen`, missing HTTP client timeouts, disabled TLS
  verification, raw logging tracebacks, raw exception text in
  logging/observability payloads, and unsafe YAML loading in source, release
  scripts, and tests,
- release workflow builds wheel and source archive from the reviewed commit,
- release artifacts pass distribution checks and `twine check`,
- no-dependency wheel install smoke passes,
- SBOM JSON and XML are generated and attached as release evidence,
- package upload directory contains only wheel and source archive files.

## Secret And Data Exposure Review

Confirm these data-handling controls:

- no token, password, or private package credential is committed,
- private registry credentials come from the CI secret store,
- logs and diagnostics avoid raw secret values,
- built-in model I/O logging is metadata-only by default,
- OTel event detail export is opt-in,
- durable event and usage records have a documented retention policy,
- incident reports do not require public disclosure of sensitive details.

## Workflow And Release Review

Confirm these workflow controls:

- workflow permissions are minimal,
- checkout credentials are not persisted,
- workflow actions are SHA-pinned,
- release tags are validated before publish,
- release verification runs before publish,
- private RC publication uses configured private package repository settings,
- public package index publication is out of scope until pilot approval.

## Pilot Security Controls

Confirm the controlled pilot uses:

- pilot profile defaults for guardrails, permissions, and governance,
- fail-closed unknown-role permission behavior from the pilot profile helper,
- explicit permission approvals for destructive or high-risk tools,
- durable event and usage evidence,
- audit evidence for `governance_breach`, `permission_denied`, and
  `guardrail_violation` failure classes,
- usage and cost monitoring,
- rollback target and rollback command,
- incident severity definitions,
- no widening of pilot traffic while a P0 or unresolved P1 issue exists.

## No-Go Rules

Do not publish, pilot, or promote when any of these are true:

- security review is unsigned,
- remote CI is missing or red on the reviewed commit,
- secret leak guard fails,
- dependency vulnerability guard has unresolved high or critical findings,
- security pattern guard fails,
- workflow action pinning guard fails,
- workflow hardening guard fails,
- private RC publication guard fails,
- public branding guard fails,
- release artifacts or SBOM files are missing,
- rollback target is unknown,
- pilot uses destructive tools without explicit permission policy.

## Reviewer Sign-Off Template

Use this template for the security review record:

| Field | Value |
|---|---|
| Reviewer | Pending |
| Commit | Pending |
| Remote CI run | Pending |
| Secret leak result | Pending |
| Dependency vulnerability result | Pending |
| Security pattern result | Pending |
| Workflow review result | Pending |
| Artifact/SBOM review result | Pending |
| Pilot security controls reviewed | Pending |
| Accepted security exceptions | Pending |
| Decision | Pending / Approved / Changes required |
