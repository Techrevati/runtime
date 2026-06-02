"""End-to-end EUAIActComplianceKit integration with AgentSession (S1 closer).

Exercises a high-risk-style flow: a loan-assessor session wired with the full
kit, verifying audit chain, incident capture, guardrail/hook enforcement, the
risk-registry deployment gate, and the conformity + transparency reports.
"""

from __future__ import annotations

import pytest

from techrevati.runtime import AgentSession
from techrevati.runtime.compliance import (
    AuditLogSink,
    EUAIActComplianceKit,
    InMemoryAuditBackend,
    InputSanitizationError,
    ResidualRiskLevel,
    Risk,
    RiskRegistry,
    StaticReviewQueue,
)
from techrevati.runtime.compliance.human_oversight import HumanOversightInterface
from techrevati.runtime.guardrails import GuardrailViolatedError


def _kit() -> EUAIActComplianceKit:
    return EUAIActComplianceKit.standard(
        audit_log=AuditLogSink(InMemoryAuditBackend()),
        risk_registry=RiskRegistry(
            [
                Risk(
                    id="bias",
                    description="scoring bias",
                    residual=ResidualRiskLevel.LOW,
                )
            ]
        ),
        oversight=HumanOversightInterface(StaticReviewQueue()),
    )


def test_standard_kit_defaults_present() -> None:
    kit = EUAIActComplianceKit.standard()
    assert kit.audit_log is not None
    assert kit.incident_sink is not None
    assert len(kit.hooks()) == 1  # input sanitization
    assert len(kit.guardrails()) == 1  # output integrity


def test_session_audit_captures_lifecycle_and_chain_valid() -> None:
    kit = _kit()
    factory = AgentSession(role="loan_assessor", phase="decide", compliance=kit)
    with factory.session() as session:
        assert session.run_tool("score", lambda: "approved") == "approved"
    types = [r.event_type for r in kit.audit_log.records()]
    assert "agent.started" in types
    assert "agent.tool_completed" in types
    assert kit.audit_log.verify_chain().valid


def test_output_integrity_guardrail_blocks_tampered_output() -> None:
    kit = EUAIActComplianceKit.standard(audit_log=AuditLogSink(InMemoryAuditBackend()))
    factory = AgentSession(role="r", phase="p", compliance=kit)
    with factory.session() as session:
        with pytest.raises(GuardrailViolatedError):
            session.run_tool("fetch", lambda: "evil\x00payload")


def test_input_sanitization_hook_blocks_bad_args() -> None:
    kit = EUAIActComplianceKit.standard(audit_log=AuditLogSink(InMemoryAuditBackend()))
    factory = AgentSession(role="r", phase="p", compliance=kit)
    from techrevati.runtime.hooks import HookContext

    with factory.session() as session:
        ctx = HookContext(role="r", phase="p", tool="fetch", args={"q": "x\x00y"})
        with pytest.raises(InputSanitizationError):
            session.run_tool("fetch", lambda: "ok", hook_ctx=ctx)


def test_unacceptable_risk_blocks_session() -> None:
    kit = EUAIActComplianceKit.standard(
        audit_log=AuditLogSink(InMemoryAuditBackend()),
        risk_registry=RiskRegistry(
            [Risk(id="x", description="d", residual=ResidualRiskLevel.UNACCEPTABLE)]
        ),
    )
    factory = AgentSession(role="r", phase="p", compliance=kit)
    from techrevati.runtime.compliance import RiskUnacceptableError

    with pytest.raises(RiskUnacceptableError):
        with factory.session():
            pass


def test_conformity_checklist_reflects_config() -> None:
    kit = _kit()
    checklist = kit.conformity_assessment_checklist()
    by_article = {i.article: i.satisfied for i in checklist.items}
    assert by_article["Article 12"] is True  # audit always on
    assert by_article["Article 14"] is True  # oversight configured
    assert by_article["Article 15"] is True  # sanitize + integrity on
    assert by_article["Article 9"] is True  # no unacceptable risk
    assert by_article["Article 16"] is None  # manual attestation
    md = checklist.to_markdown()
    assert "conformity self-check" in md
    assert "Not legal advice" in md


def test_transparency_report_from_kit() -> None:
    kit = _kit()
    report = kit.transparency_report(
        system_name="LoanAssistant",
        provider_contact="compliance@bank.example",
        intended_purpose="creditworthiness summaries",
        runtime_version="0.4.0.dev0",
        capabilities=("summarize",),
    )
    d = report.to_dict()
    assert d["logging_mechanism"]["tamper_evident"] is True
    assert "output integrity checks" in d["cybersecurity_measures"]
    assert d["human_oversight"]["override_enabled"] is True
    assert len(d["foreseeable_risks"]) == 1


@pytest.mark.asyncio
async def test_async_session_with_kit() -> None:
    kit = _kit()
    factory = AgentSession(role="r", phase="p", compliance=kit)

    async def _ok() -> str:
        return "done"

    async with factory.asession() as session:
        await session.arun_tool("fetch", _ok)
    assert kit.audit_log.verify_chain().valid
