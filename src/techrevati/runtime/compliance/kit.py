"""
EUAIActComplianceKit — one-stop facade wiring the Article 9/12/13/14/15/26 primitives.

A deployer building a high-risk system on techrevati-runtime configures one
:class:`EUAIActComplianceKit` and passes it to ``AgentSession(compliance=kit)``.
The kit bundles:

- :class:`~techrevati.runtime.compliance.audit_log.AuditLogSink` (Article 12)
- :class:`~techrevati.runtime.compliance.risk_registry.RiskRegistry` (Article 9)
- :class:`~techrevati.runtime.compliance.human_oversight.HumanOversightInterface`
  (Article 14)
- :class:`~techrevati.runtime.compliance.incidents.IncidentReportingSink`
  (Articles 26 + 73)
- input-sanitization hooks + output-integrity guardrails (Article 15)

``AgentSession`` fans the audit log and incident sink alongside the caller's own
sinks, prepends the kit's guardrails/hooks, and asserts the risk registry has no
unacceptable residual risk before the session opens.

.. warning::

    Engineering primitive, not legal advice. The kit structures evidence and
    controls; it does not certify compliance. The deployer remains responsible for
    classification, conformity assessment, and operation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from techrevati.runtime.compliance.audit_log import (
    AuditLogSink,
    InMemoryAuditBackend,
)
from techrevati.runtime.compliance.cybersecurity import (
    InputSanitizationHook,
    OutputIntegrityGuardrail,
)
from techrevati.runtime.compliance.human_oversight import HumanOversightInterface
from techrevati.runtime.compliance.incidents import (
    IncidentReportingSink,
    SeriousIncidentDetector,
)
from techrevati.runtime.compliance.risk_registry import RiskRegistry
from techrevati.runtime.compliance.transparency import (
    AccuracyDeclaration,
    HumanOversightConfig,
    TransparencyReport,
)

__all__ = [
    "ConformityChecklist",
    "ConformityItem",
    "EUAIActComplianceKit",
]


@dataclass(frozen=True)
class ConformityItem:
    """One line of the Article 16 conformity self-check.

    ``satisfied`` is ``True``/``False`` when the kit can determine it from
    configuration, or ``None`` when it is a manual deployer attestation.
    """

    article: str
    requirement: str
    satisfied: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "article": self.article,
            "requirement": self.requirement,
            "satisfied": self.satisfied,
        }


@dataclass(frozen=True)
class ConformityChecklist:
    """Article 16 conformity self-check, rendered for the deployer to confirm."""

    items: tuple[ConformityItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"items": [i.to_dict() for i in self.items]}

    def to_markdown(self) -> str:
        def mark(value: bool | None) -> str:
            return {True: "✅", False: "❌", None: "⬜ (manual)"}[value]

        lines = [
            "# EU AI Act conformity self-check",
            "",
            "> ⚠️ **Not legal advice.** A self-check of technical controls, not a "
            "conformity assessment. ⬜ items require the deployer's attestation.",
            "",
        ]
        for item in self.items:
            lines.append(
                f"- {mark(item.satisfied)} **{item.article}** — {item.requirement}"
            )
        return "\n".join(lines)


@dataclass
class EUAIActComplianceKit:
    """Bundle of EU AI Act primitives for ``AgentSession(compliance=...)``."""

    audit_log: AuditLogSink
    risk_registry: RiskRegistry | None = None
    oversight: HumanOversightInterface | None = None
    incident_sink: IncidentReportingSink | None = None
    input_hooks: tuple[InputSanitizationHook, ...] = ()
    output_guardrails: tuple[OutputIntegrityGuardrail, ...] = ()

    @classmethod
    def standard(
        cls,
        *,
        audit_log: AuditLogSink | None = None,
        risk_registry: RiskRegistry | None = None,
        oversight: HumanOversightInterface | None = None,
        detector: SeriousIncidentDetector | None = None,
        sanitize_inputs: bool = True,
        check_output_integrity: bool = True,
        max_chars: int | None = None,
    ) -> EUAIActComplianceKit:
        """Build a kit with sensible defaults; override individual pieces.

        Defaults: in-memory audit log (pass a ``SqliteAuditBackend``-backed
        ``AuditLogSink`` for durability), incident detection wired to the audit
        log, plus input-sanitization + output-integrity (Article 15) enabled.
        """
        log = audit_log or AuditLogSink(InMemoryAuditBackend())
        incident_sink = IncidentReportingSink(
            detector or SeriousIncidentDetector(), audit_log=log
        )
        input_hooks = (
            (InputSanitizationHook(max_chars=max_chars),) if sanitize_inputs else ()
        )
        output_guardrails = (
            (OutputIntegrityGuardrail(max_chars=max_chars),)
            if check_output_integrity
            else ()
        )
        return cls(
            audit_log=log,
            risk_registry=risk_registry,
            oversight=oversight,
            incident_sink=incident_sink,
            input_hooks=input_hooks,
            output_guardrails=output_guardrails,
        )

    # -- wiring helpers consumed by AgentSession ----------------------------

    def event_sinks(self) -> list[Any]:
        """Event sinks to fan out alongside the caller's sink."""
        sinks: list[Any] = [self.audit_log]
        if self.incident_sink is not None:
            sinks.append(self.incident_sink)
        return sinks

    def usage_sinks(self) -> list[Any]:
        """Usage sinks to fan out (only the audit log records usage)."""
        return [self.audit_log]

    def guardrails(self) -> list[Any]:
        return list(self.output_guardrails)

    def hooks(self) -> list[Any]:
        return list(self.input_hooks)

    def assert_deployable(self) -> None:
        """Raise if the risk registry contains an unacceptable residual risk."""
        if self.risk_registry is not None:
            self.risk_registry.assert_no_unacceptable()

    # -- reporting ----------------------------------------------------------

    def transparency_report(
        self,
        *,
        system_name: str,
        provider_contact: str,
        intended_purpose: str,
        runtime_version: str,
        capabilities: Sequence[str] = (),
        limitations: Sequence[str] = (),
        accuracy_declarations: Sequence[AccuracyDeclaration] = (),
    ) -> TransparencyReport:
        """Build an Article 13 transparency report from the kit's configuration."""
        measures: list[str] = []
        if self.input_hooks:
            measures.append("input sanitization (control/escape byte rejection)")
        if self.output_guardrails:
            measures.append("output integrity checks")
        oversight_cfg = None
        if self.oversight is not None:
            oversight_cfg = HumanOversightConfig(
                override_enabled=True,
                reviewer_authentication="caller-defined",
            )
        risks = (
            tuple(r.to_dict() for r in self.risk_registry)
            if self.risk_registry is not None
            else ()
        )
        return TransparencyReport(
            system_name=system_name,
            runtime_version=runtime_version,
            provider_contact=provider_contact,
            intended_purpose=intended_purpose,
            capabilities=tuple(capabilities),
            limitations=tuple(limitations),
            accuracy_declarations=tuple(accuracy_declarations),
            cybersecurity_measures=tuple(measures),
            foreseeable_risks=risks,
            human_oversight=oversight_cfg,
            logging_mechanism={
                "sink": "AuditLogSink",
                "tamper_evident": True,
                "backend": type(self.audit_log).__name__,
            },
        )

    def conformity_assessment_checklist(self) -> ConformityChecklist:
        """Article 16 self-check derived from the kit's configuration."""
        no_unacceptable: bool | None
        if self.risk_registry is None:
            no_unacceptable = None
        else:
            no_unacceptable = len(self.risk_registry.unacceptable()) == 0
        items = (
            ConformityItem(
                "Article 9",
                "Risk management system with acceptable residual risk",
                no_unacceptable,
            ),
            ConformityItem(
                "Article 12",
                "Tamper-evident, retained record-keeping (audit log)",
                True,
            ),
            ConformityItem(
                "Article 14",
                "Human oversight (pause / review / override) configured",
                self.oversight is not None,
            ),
            ConformityItem(
                "Article 15",
                "Robustness: input sanitization + output integrity",
                bool(self.input_hooks) and bool(self.output_guardrails),
            ),
            ConformityItem(
                "Articles 26/73",
                "Incident detection + 15-day reporting tracking",
                self.incident_sink is not None,
            ),
            ConformityItem(
                "Article 10",
                "Data governance for training/validation data",
                None,
            ),
            ConformityItem(
                "Article 13",
                "Instructions for use provided to the deployer",
                None,
            ),
            ConformityItem(
                "Article 16",
                "Conformity assessment completed and CE marking applied",
                None,
            ),
        )
        return ConformityChecklist(items=items)
