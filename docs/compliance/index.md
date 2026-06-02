# Compliance Mapping

Author: Techrevati doo

This page is a technical control crosswalk for teams preparing their own
deployment review. It is not legal advice and does not make the runtime
compliant by itself; the deployer remains responsible for model choice,
data handling, user disclosures, monitoring, incident response, and records.

## Runtime Controls

| Control area | Runtime primitive | Evidence produced |
|---|---|---|
| Human stop conditions | `GovernancePlane`, `MaxIterationsLimit`, `MaxBudgetLimit`, `MaxToolCallsLimit` | `governance.breach` and `agent.failed` events |
| Alert before hard stop | `on_breach="alert"` | `governance.alert` events with limit metadata |
| Record keeping | `AgentEvent`, `SqliteEventSink`, `SqliteUsageSink` | JSON event payloads, usage snapshots, durable sink rows |
| Traceability | `project_id`, `role`, `phase`, `thread_id`, `idempotency_key` | Joined event, checkpoint, and usage records |
| Robustness | `CircuitBreaker`, `RateLimiter`, `max_iterations`, `UsageLimits` | Typed failure classes and bounded execution |
| Recovery review | `RecoveryContext`, recovery outcome events | Attempted/succeeded/failed/escalated recovery events |
| Tool control | `PermissionEnforcer`, guardrails, hooks | `agent.blocked`, `agent.tool_called`, `agent.tool_completed` |
| Release evidence | Release workflow, distribution checker, SBOM artifacts | Wheel/sdist validation, metadata check, CycloneDX SBOM |
| Tamper-evident record-keeping (Art. 12) | `AuditLogSink` (`SqliteAuditBackend`) | Hash-chained records; `verify_chain()` detects edits/deletes/reorders |
| Human oversight (Art. 14) | `HumanOversightInterface`, `ReviewQueue` | `oversight.review_requested` / `oversight.review_resolved` with reviewer id |
| Risk management (Art. 9) | `RiskRegistry`, `Risk`, `ResidualRiskLevel` | Residual-risk register; deployment blocked on `UNACCEPTABLE` |
| Robustness (Art. 15) | `OutputIntegrityGuardrail`, `InputSanitizationHook` | Blocked tool I/O carrying control/escape bytes |
| Incident reporting (Art. 26/73) | `IncidentReportingSink`, `SeriousIncidentDetector` | `IncidentReport` with 15-day reporting deadline |
| Transparency (Art. 13) | `TransparencyReport`, `AccuracyDeclaration` | Instructions-for-use markdown / dict |

For the full EU AI Act compliance kit (the `EUAIActComplianceKit` facade,
article-by-article guidance, and the audit-log threat model) see the dedicated
[EU AI Act](../eu-ai-act/index.md) section.

## Review Checklist

1. Configure `GovernancePlane` with hard-stop limits for unattended sessions.
2. Route events to a durable sink before enabling high-volume or regulated use.
3. Attach a stable `project_id` and `thread_id` to every resumable workflow.
4. Keep pricing and usage limits caller-owned and reviewed before rollout.
5. Use `PermissionEnforcer` for role/tool policy and guardrails for content gates.
6. Verify release artifacts and SBOM files before deploying a new version.
7. Document which events are retained, for how long, and who can access them.
8. Follow the [Production Readiness Plan](production-readiness.md) and
   [Release Candidate Inventory](rc-inventory.md) before promoting a release
   candidate to stable production use.

## Boundaries

- Permissions and guardrails are application-level gates, not process sandboxes.
- Usage snapshots are caller supplied; validate upstream accounting if billing
  or contractual limits depend on them.
- SQLite reference sinks are suitable for local durability and tests. For
  fleet-wide retention, wrap the sink protocols with the deployment database or
  log pipeline.
- The runtime records technical events; it does not provide legal classification,
  user-notice flows, or policy decisions for a specific deployment.
