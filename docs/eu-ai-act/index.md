# EU AI Act compliance kit

> ⚠️ **Not legal advice.** This documentation describes engineering primitives
> that map to EU AI Act (Regulation (EU) 2024/1689) technical requirements.
> Whether your system is in scope, what risk category applies, and which exact
> obligations bind you depend on facts and law this library cannot assess.
> Consult qualified counsel and, where applicable, your national competent
> authority (Article 70).

`techrevati-runtime` is **not itself an AI system** — it is infrastructure. But a
deployer building a high-risk system on it must provide the technical controls
that Articles 9–15 and 26 require. The `techrevati.runtime.compliance` subpackage
ships those controls as composable primitives, bundled behind one facade:
`EUAIActComplianceKit`.

## Article → primitive crosswalk

| Article | Requirement | Primitive |
|---|---|---|
| **Art. 9** | Risk management system | [`RiskRegistry`](risk-management.md) — declare risks, residual levels, review cadence; blocks deployment on `UNACCEPTABLE` |
| **Art. 12** | Record-keeping | [`AuditLogSink`](audit-log.md) — tamper-evident, hash-chained event + usage log |
| **Art. 13** | Transparency to deployers | [`TransparencyReport`](transparency.md) — instructions-for-use markdown |
| **Art. 14** | Human oversight | [`HumanOversightInterface`](human-oversight.md) — pause / review / override |
| **Art. 15** | Robustness & cybersecurity | [`OutputIntegrityGuardrail` + `InputSanitizationHook`](cybersecurity.md), plus existing `PromptInjectionGuardrail`, `CircuitBreaker`, `RateLimiter` |
| **Art. 26 / 73** | Incident monitoring & reporting | [`IncidentReportingSink` + `SeriousIncidentDetector`](incident-reporting.md) — 15-day deadline tracking |
| **Art. 16** | Conformity assessment | [`ConformityChecklist`](conformity-checklist.md) — self-check derived from the kit |

## Quickstart

```python
from techrevati.runtime import AgentSession
from techrevati.runtime.compliance import (
    AuditLogSink, SqliteAuditBackend, EUAIActComplianceKit,
    RiskRegistry, Risk, ResidualRiskLevel,
)

kit = EUAIActComplianceKit.standard(
    audit_log=AuditLogSink(SqliteAuditBackend("audit.db")),   # durable Article 12
    risk_registry=RiskRegistry([
        Risk(id="bias", description="scoring bias",
             residual=ResidualRiskLevel.LOW, affected_articles=("art.10",)),
    ]),
)

session = AgentSession(role="loan_assessor", phase="decide", compliance=kit)
with session.session() as s:
    result = s.run_tool("score", lambda: assess(application))

assert kit.audit_log.verify_chain().valid          # tamper-evidence holds
print(kit.conformity_assessment_checklist().to_markdown())
```

`AgentSession(compliance=kit)` automatically:

- fans every event + usage record into the audit log (Article 12) and the
  incident sink (Articles 26/73), alongside your own sinks;
- prepends the kit's output-integrity guardrail and input-sanitization hook
  (Article 15);
- asserts the risk registry has no `UNACCEPTABLE` residual risk before the
  session opens (Article 9(4)).

## What this is **not**

- Not a statement that the runtime is "EU AI Act compliant" — only a deployment
  can be.
- Not a guarantee of compliance — conformity assessment, data governance,
  training, and monitoring remain the deployer's responsibility.
- Not certification — we are not a notified body.
