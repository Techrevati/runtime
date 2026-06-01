# Controlled RC Pilot Evidence Template

Author: Techrevati doo

Use this template to record the evidence for a controlled `0.3.0rc1` pilot.
The template is intentionally strict: incomplete evidence is a no-go for stable
`0.3.0`.

Use `docs/compliance/pilot-execution.md` for the real controlled pilot launch
and execution checklist. Local dry-run output can support setup review, but it
does not replace real controlled pilot evidence.

## Pilot Identification

| Field | Value |
|---|---|
| Package version | `0.3.0rc1` |
| Commit SHA | |
| Pilot workflow | |
| Pilot owner | |
| Operator on duty | |
| Start time | |
| End time | |
| Private artifact source | |
| Previous known-good version | |
| Rollback artifact source | |

## Preconditions

- [ ] Local/server production gate is green.
- [ ] Remote CI is green on the pilot commit.
- [ ] Package artifacts are built and verified.
- [ ] SBOM JSON and XML are attached as release evidence.
- [ ] SHA256SUMS is attached and verifies wheel, source archive, and SBOM
  files.
- [ ] Public branding guard passes.
- [ ] Secret leak guard passes.
- [ ] Dependency vulnerability guard passes.
- [ ] Pilot operations runbook has been reviewed.
- [ ] Pilot profile values are approved.
- [ ] Rollback target and rollback command are recorded.
- [ ] Pilot user group and request-volume cap are recorded.

## Runtime Configuration

Record the effective runtime configuration:

| Setting | Value |
|---|---|
| Role | |
| Phase | |
| Allowed tools | |
| Permission mode | |
| Budget USD | |
| Enforce budget | |
| Max iterations | |
| Max tool calls | |
| Max consecutive failures | |
| Event sink | |
| Usage sink | |
| Checkpoint saver | |
| OTel exporter | |
| OTel event detail enabled | `false` |
| Telemetry retention | |
| Durable record retention | |

## Controlled Scenarios

Run each scenario before a stable go decision.

| Scenario | Expected evidence | Result | Notes |
|---|---|---|---|
| Successful session | `agent.started`, `agent.completed`, usage record, latency sample | | |
| Prompt-injection attempt | Guardrail block or rejection without prompt leakage | | |
| Permission denial | `agent.blocked` with `kind=permission`; terminal path uses `failure_class=permission_denied` | | |
| Guardrail block | `agent.blocked` with `kind=guardrail`; terminal path uses `failure_class=guardrail_violation` | | |
| Max-iterations breach | `governance.breach` and terminal `failure_class=governance_breach` are visible | | |
| Max-tool-calls breach | `governance.breach` with tool-call limit metadata and `failure_class=governance_breach` | | |
| Provider failover | Recovery attempt and provider-switch evidence | | |
| Checkpoint resume | Checkpoint write and replay/resume proof | | |
| Sink failure diagnostic | Session continues and sink failure diagnostic is visible | | |
| Rollback to previous known-good version | Rollback proof checklist completed | | |

## Signal Evidence

Attach the query output or metric screenshot for each signal:

| Signal | Evidence reference | Pass/Fail | Notes |
|---|---|---|---|
| Session count | | | |
| Success rate | | | |
| Failure rate | | | |
| Failure-class distribution | | | |
| Guardrail blocks | | | |
| Permission denials | | | |
| Governance breaches | | | |
| Retry attempts | | | |
| Provider switches | | | |
| Token usage | | | |
| Estimated cost | | | |
| Tool call count | | | |
| Checkpoint writes | | | |
| Checkpoint replays | | | |
| Event sink failures | | | |
| Usage sink failures | | | |
| Turn latency | | | |
| Tool-call latency | | | |

## Incident Review

| Severity | Count | Unresolved count | Notes |
|---|---:|---:|---|
| P0 | | | |
| P1 | | | |
| P2 | | | |
| P3 | | | |

No-go conditions:

- Any P0 incident.
- Any unresolved P1 incident.
- Unknown data exposure.
- Missing rollback proof.
- Missing durable event or usage evidence.
- Missing cost evidence.
- Public branding guard failure.
- Secret leak guard failure.

## Success Criteria

- [ ] Zero P0 incidents.
- [ ] Zero unresolved P1 incidents.
- [ ] At least 99 percent of sessions complete without runtime crash.
- [ ] Blocked, denied, breached, and recovered actions are audit-ready.
- [ ] Policy and safety stops carry the expected terminal failure classes.
- [ ] Usage and cost tracking are explainable.
- [ ] Recovery and provider failover behavior is visible.
- [ ] Rollback has been tested and documented.
- [ ] Diagnostic bundle is complete and stored in restricted release evidence.

## Rollback Evidence

Link the completed rollback proof checklist:

- Rollback proof checklist:
- Rollback target:
- Rollback command:
- Rollback smoke result:
- Rollback operator:
- Rollback timestamp:

## Diagnostic Bundle

Attach or link:

- package version and commit SHA,
- production gate evidence,
- remote CI status,
- SHA256SUMS verification output,
- `events.db`,
- `usage.db`,
- relevant checkpoint IDs or a redacted `checkpoints.db`,
- process logs for the pilot window,
- OTel trace or metric export for the pilot window,
- configured pilot profile values,
- alert evidence or alert dry-run evidence,
- rollback target and rollback command.

Do not include prompts, tool arguments, tool outputs, raw secrets, credentials,
or unredacted customer data in the bundle.

## Go/No-Go Decision

Decision: `go` / `no-go`

Reason:

Stable release blockers:

Follow-up owner:

Approval:

Date:
