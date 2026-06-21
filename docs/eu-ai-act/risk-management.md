# Risk management — Article 9

> ⚠️ **Not legal advice.** Residual-risk levels and review cadences are the
> deployer's determination.

Article 9 requires a continuous, iterative risk-management process. `RiskRegistry`
is the bookkeeping primitive: declare each `Risk`, point it at a mitigation,
record the residual level, and flag reviews that come due.

```python
from datetime import timedelta
from techrevati.runtime.compliance import (
    RiskRegistry, Risk, ResidualRiskLevel, RiskUnacceptableError,
)

registry = RiskRegistry([
    Risk(
        id="hallucination",
        description="model fabricates loan terms",
        residual=ResidualRiskLevel.MEDIUM,
        affected_articles=("art.15",),
        mitigation_recipe="LLM_ERROR",         # a RecoveryRecipe scenario name
        review_interval=timedelta(days=90),
    ),
])

# Article 9(4): unacceptable residual risk blocks deployment.
registry.assert_no_unacceptable()              # raises RiskUnacceptableError

# Continuous review — which entries are overdue?
for risk in registry.review_due():
    schedule_review(risk)
```

`ResidualRiskLevel` ranges `NEGLIGIBLE → LOW → MEDIUM → HIGH → UNACCEPTABLE`.
When a `RiskRegistry` is attached via `EUAIActComplianceKit`, the
`AgentSession(compliance=kit)` calls `assert_no_unacceptable()` before opening a
session, so an unacceptable residual risk hard-blocks execution.

`mitigation_recipe` is a free-form name that, by convention, references one of the
runtime's `RecoveryRecipe` scenarios so the mitigation is explicit and traceable.
