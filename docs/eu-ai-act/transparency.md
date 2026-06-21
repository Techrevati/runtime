# Transparency to deployers — Article 13

> ⚠️ **Not legal advice.** The declarations are the deployer's responsibility;
> this library only structures and renders them.

Article 13 requires high-risk systems to ship with instructions for use covering
provider identity, capabilities, limitations, performance, foreseeable risks,
human-oversight measures, and a description of the Article 12 logging mechanism.

`TransparencyReport` is a serializable container that renders to markdown (the
default instructions-for-use format). The runtime cannot *measure* accuracy — you
declare it via `AccuracyDeclaration` (Article 15(3)).

```python
from datetime import UTC, datetime
from techrevati.runtime.compliance import AccuracyDeclaration

report = kit.transparency_report(
    system_name="LoanAssistant",
    provider_contact="compliance@bank.example",
    intended_purpose="Assist loan officers with creditworthiness summaries.",
    runtime_version="0.4.0",
    capabilities=("summarize applications", "flag missing documents"),
    limitations=("not a final decision-maker",),
    accuracy_declarations=(
        AccuracyDeclaration(
            metric_name="precision", metric_value=0.91,
            measured_on_dataset="2026Q1-holdout",
            measured_date=datetime(2026, 4, 1, tzinfo=UTC),
            confidence_interval=(0.88, 0.94),
        ),
    ),
)

open("instructions-for-use.md", "w").write(report.to_markdown())
```

Built from the kit, the report auto-fills the cybersecurity measures (from the
configured guardrails/hooks), the human-oversight summary, the foreseeable risks
(from the risk registry), and the logging-mechanism description (the audit sink).
