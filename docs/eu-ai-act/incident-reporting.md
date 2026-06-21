# Incident reporting — Articles 26 & 73

> ⚠️ **Not legal advice.** What counts as a "serious incident" and the exact
> reporting deadline are legal determinations for the deployer.

Article 26 obliges deployers to monitor for malfunctions and serious incidents;
Article 73 requires reporting serious incidents to the competent authority,
generally within 15 days of awareness.

## Detection

`SeriousIncidentDetector` is a composable rule set over `AgentEvent`s. The default
rule classifies governance breaches and terminal failures as `MALFUNCTION`; add
rules to escalate domain-specific events to `SERIOUS` (the highest severity wins).

```python
from techrevati.runtime.compliance import (
    SeriousIncidentDetector, IncidentSeverity, IncidentReportingSink,
)

detector = SeriousIncidentDetector()

def fundamental_rights(event):
    if event.detail and "rights breach" in event.detail:
        return IncidentSeverity.SERIOUS
    return None

detector.add(fundamental_rights)
```

## Reporting sink

`IncidentReportingSink` is an `EventSink` — incidents derive from `AgentEvent`s
(including `governance.breach` and `agent.failed`), which flow through the event
sink, not the hook chain. It materializes an `IncidentReport` per detected event
and mirrors it to the audit log.

```python
sink = IncidentReportingSink(detector, audit_log=kit.audit_log,
                             on_incident=notify_dpo)

for report in sink.incidents:
    print(report.id, report.severity, report.reporting_deadline)
    if report.overdue():
        escalate(report)            # past the 15-day Article 73 window
```

`EUAIActComplianceKit.standard()` wires an `IncidentReportingSink` to the audit
log automatically.
