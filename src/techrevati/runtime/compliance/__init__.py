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

__all__ = [
    "AuditBackend",
    "AuditLogSink",
    "AuditRecord",
    "ChainVerification",
    "InMemoryAuditBackend",
    "RetentionPolicy",
    "SqliteAuditBackend",
]
