"""
Compliance — EU AI Act technical primitives for high-risk deployments.

This subpackage provides the runtime building blocks that map to the EU AI Act
(Regulation (EU) 2024/1689) technical requirements:

- Article 12 (record-keeping): :class:`AuditLogSink` — a tamper-evident,
  hash-chained event + usage log.

More primitives (human oversight, risk registry, transparency, incident
reporting) land in later 0.4.0 sprints and are re-exported here as they arrive,
together with the :class:`EUAIActComplianceKit` facade.

.. warning::

    This is engineering documentation, not legal advice. The runtime is not
    itself an AI system; it provides primitives a deployer composes into a
    compliant high-risk system. Whether your system is in scope, and which
    obligations bind you, depend on facts and law this library cannot assess.
    Consult qualified counsel.
"""

from __future__ import annotations

from techrevati.runtime.compliance.audit_log import (
    AuditBackend,
    AuditLogSink,
    AuditRecord,
    ChainVerification,
    InMemoryAuditBackend,
    RetentionPolicy,
    SqliteAuditBackend,
)
from techrevati.runtime.compliance.cybersecurity import (
    InputSanitizationError,
    InputSanitizationHook,
    OutputIntegrityGuardrail,
)
from techrevati.runtime.compliance.human_oversight import (
    ExplanationReport,
    HumanOversightInterface,
    ReviewDecision,
    ReviewerIdentity,
    ReviewQueue,
    ReviewTimeoutError,
    StaticReviewQueue,
)
from techrevati.runtime.compliance.incidents import (
    IncidentReport,
    IncidentReportingSink,
    IncidentSeverity,
    SeriousIncidentDetector,
)
from techrevati.runtime.compliance.kit import (
    ConformityChecklist,
    ConformityItem,
    EUAIActComplianceKit,
)
from techrevati.runtime.compliance.risk_registry import (
    ResidualRiskLevel,
    Risk,
    RiskRegistry,
    RiskUnacceptableError,
)
from techrevati.runtime.compliance.transparency import (
    AccuracyDeclaration,
    HumanOversightConfig,
    TransparencyReport,
)

__all__ = [
    # Article 12 — record-keeping
    "AuditBackend",
    "AuditLogSink",
    "AuditRecord",
    "ChainVerification",
    "InMemoryAuditBackend",
    "RetentionPolicy",
    "SqliteAuditBackend",
    # Article 14 — human oversight
    "ExplanationReport",
    "HumanOversightInterface",
    "ReviewDecision",
    "ReviewQueue",
    "ReviewTimeoutError",
    "ReviewerIdentity",
    "StaticReviewQueue",
    # Article 9 — risk management
    "ResidualRiskLevel",
    "Risk",
    "RiskRegistry",
    "RiskUnacceptableError",
    # Article 15 — robustness / cybersecurity
    "InputSanitizationError",
    "InputSanitizationHook",
    "OutputIntegrityGuardrail",
    # Article 26 + 73 — incident reporting
    "IncidentReport",
    "IncidentReportingSink",
    "IncidentSeverity",
    "SeriousIncidentDetector",
    # Article 13 — transparency
    "AccuracyDeclaration",
    "HumanOversightConfig",
    "TransparencyReport",
    # Facade (Article 16 one-stop)
    "ConformityChecklist",
    "ConformityItem",
    "EUAIActComplianceKit",
]
