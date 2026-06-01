# Pilot Execution Checklist

Author: Techrevati doo

Pilot execution status: Pending until the real controlled pilot is complete.

This checklist controls the real `0.3.0rc1` downstream pilot. It is not a local
dry-run record and must not be completed from `scripts/check_pilot_dry_run.py`
output alone.

## Purpose

The local pilot dry-run proves runtime wiring. The real controlled pilot proves
that the release candidate behaves safely in a bounded downstream workflow with
operators, evidence retention, incident handling, and rollback readiness.

## Pilot Preflight Snapshot

Latest pilot preflight snapshot collected on 2026-06-01 before the real
controlled pilot:

- branch: `codex/production-rc-0.3.0`,
- base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,
- current working-tree release diff: 85 files changed, 8,859 insertions,
  1,794 deletions,
- untracked release assets: 104 files, all classified by
  `docs/compliance/staging-manifest.md`,
- release candidate: `0.3.0rc1`,
- local/server full production gate: 999 tests passed with 94.85 percent total
  coverage,
- pilot profile helper: documented and covered by tests,
- pilot dry-run command: `python scripts/check_pilot_dry_run.py`,
- pilot dry-run result: passed with 10 local scenarios,
- covered local scenarios: successful session, prompt-injection attempt,
  permission denial, guardrail block, max-iterations breach, max-tool-calls
  breach, provider failover evidence, checkpoint resume, sink failure
  diagnostic, and rollback readiness command shape,
- pilot execution guard: passed,
- rollback execution guard: passed,
- real controlled pilot evidence, downstream telemetry, private artifact
  installation, operator review, and real rollback proof: Pending.

This is preflight evidence only. It proves local pilot wiring and checklist
readiness, but it does not approve stable promotion and must not be used as the
only pilot evidence.

## Dry-Run Boundary

Do not use local dry-run output as pilot evidence by itself. The dry-run can be
attached as setup evidence, but it does not replace the controlled pilot,
downstream telemetry, private artifact installation, operator review, or real
rollback proof.

## Launch Preconditions

All of these must be true before starting the controlled pilot:

- full local/server production gate is green,
- remote CI validation checklist is complete for the reviewed commit,
- final reviewer handoff is complete,
- security review checklist is complete,
- private RC publication evidence is available,
- pilot operations runbook has been reviewed,
- controlled pilot evidence template is prepared,
- rollback proof checklist has a recorded previous known-good target,
- pilot owner, operator on duty, and request-volume cap are recorded,
- pilot workflow has no destructive tool action without explicit permission.

## Pilot Window

Record the pilot window before launch:

| Field | Value |
|---|---|
| Pilot owner | Pending |
| Operator on duty | Pending |
| Workflow | Pending |
| Start time | Pending |
| End time | Pending |
| Request-volume cap | Pending |
| Allowed users | Pending |
| Private artifact source | Pending |
| Previous known-good version | Pending |
| Rollback command | Pending |

Use `docs/compliance/rollback-execution.md` for the rollback execution
checklist and operator sign-off.

## Required Execution Scenarios

Run these scenarios against the downstream pilot workflow:

- successful session,
- prompt-injection attempt,
- permission denial,
- guardrail block,
- max-iterations breach,
- max-tool-calls breach,
- provider failover,
- checkpoint resume,
- sink failure diagnostic,
- rollback to previous known-good version.

## Required Evidence

Attach restricted evidence for:

- durable event records,
- durable usage records,
- checkpoint write and replay proof,
- telemetry or metric export for the pilot window,
- guardrail block records,
- permission denial records,
- governance breach records,
- provider failover records,
- token usage and estimated cost,
- turn latency and tool-call latency,
- alert evidence or alert dry-run evidence,
- diagnostic bundle with prompts, tool arguments, tool outputs, raw secrets,
  credentials, and customer data redacted.

## Incident Handling

Classify every pilot finding as P0, P1, P2, or P3. Stop pilot expansion when any
P0 occurs or any P1 remains unresolved. Record the owner, severity, mitigation,
and regression-test requirement for every stable-blocking finding.

## Go/No-Go Rules

Stable `0.3.0` is no-go when any of these are true:

- pilot execution status is still pending,
- local dry-run output is the only pilot evidence,
- any P0 incident occurred,
- any P1 incident remains unresolved,
- durable event or usage evidence is missing,
- cost evidence is missing,
- rollback proof is incomplete,
- private RC artifact source is unknown,
- pilot telemetry is missing,
- diagnostic bundle contains raw secrets or unredacted customer data.

## Sign-Off Template

Use this template for the pilot execution record:

| Field | Value |
|---|---|
| Pilot owner | Pending |
| Operator on duty | Pending |
| Commit SHA | Pending |
| Package version | `0.3.0rc1` |
| Pilot window | Pending |
| Request-volume cap | Pending |
| Evidence bundle location | Pending |
| P0 incidents | Pending |
| Unresolved P1 incidents | Pending |
| Rollback proof result | Pending |
| Decision | Pending / Approved / Changes required |
