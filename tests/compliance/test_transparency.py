"""Tests for the transparency report (EU AI Act Article 13)."""

from __future__ import annotations

from datetime import UTC, datetime

from techrevati.runtime.compliance import (
    AccuracyDeclaration,
    HumanOversightConfig,
    TransparencyReport,
)


def _report() -> TransparencyReport:
    return TransparencyReport(
        system_name="LoanAssistant",
        runtime_version="0.4.0.dev0",
        provider_contact="compliance@bank.example",
        intended_purpose="Assist loan officers with creditworthiness summaries.",
        capabilities=("summarize applications", "flag missing documents"),
        limitations=("not a final decision-maker",),
        accuracy_declarations=(
            AccuracyDeclaration(
                metric_name="precision",
                metric_value=0.91,
                measured_on_dataset="2026Q1-holdout",
                measured_date=datetime(2026, 4, 1, tzinfo=UTC),
                confidence_interval=(0.88, 0.94),
            ),
        ),
        cybersecurity_measures=("prompt-injection guardrail", "output integrity"),
        human_oversight=HumanOversightConfig(
            pause_conditions=("budget_threshold",),
            override_enabled=True,
            reviewer_authentication="oauth",
        ),
        logging_mechanism={"sink": "AuditLogSink", "tamper_evident": True},
    )


def test_to_dict_roundtrip() -> None:
    d = _report().to_dict()
    assert d["system_name"] == "LoanAssistant"
    assert d["accuracy_declarations"][0]["metric_value"] == 0.91
    assert d["human_oversight"]["override_enabled"] is True
    assert d["logging_mechanism"]["tamper_evident"] is True


def test_to_markdown_includes_key_sections() -> None:
    md = _report().to_markdown()
    assert "Instructions for Use — LoanAssistant" in md
    assert "Not legal advice" in md
    assert "## Capabilities" in md
    assert "precision: 0.91" in md
    assert "## Human oversight" in md
    assert "## Logging mechanism (Article 12)" in md


def test_minimal_report_renders() -> None:
    report = TransparencyReport(
        system_name="Minimal",
        runtime_version="0.4.0.dev0",
        provider_contact="x@y.z",
        intended_purpose="demo",
    )
    md = report.to_markdown()
    assert "Minimal" in md
    assert "Not configured" in md  # no human oversight configured
