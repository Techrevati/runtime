"""
Transparency report — information for deployers (EU AI Act Article 13).

Article 13 requires high-risk AI systems to ship with instructions for use that
let a deployer understand and correctly operate the system: provider identity,
capabilities and limitations, performance characteristics, foreseeable risks,
human-oversight measures, and a description of the Article 12 logging mechanism.

:class:`TransparencyReport` is a serializable container for that information.
The runtime cannot *measure* accuracy — the deployer declares it via
:class:`AccuracyDeclaration`. The report renders to markdown (the default
instructions-for-use format) and to a plain dict.

.. warning::

    Engineering primitive, not legal advice. The declarations are the deployer's
    responsibility; this library only structures and renders them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "AccuracyDeclaration",
    "HumanOversightConfig",
    "TransparencyReport",
]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AccuracyDeclaration:
    """A caller-declared performance metric (Article 15(3))."""

    metric_name: str
    metric_value: float
    measured_on_dataset: str
    measured_date: datetime
    confidence_interval: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "measured_on_dataset": self.measured_on_dataset,
            "measured_date": self.measured_date.isoformat(),
            "confidence_interval": (
                list(self.confidence_interval)
                if self.confidence_interval is not None
                else None
            ),
        }


@dataclass(frozen=True)
class HumanOversightConfig:
    """Summary of the configured human-oversight measures (Article 14 → 13)."""

    pause_conditions: tuple[str, ...] = ()
    override_enabled: bool = False
    reviewer_authentication: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "pause_conditions": list(self.pause_conditions),
            "override_enabled": self.override_enabled,
            "reviewer_authentication": self.reviewer_authentication,
        }


@dataclass(frozen=True)
class TransparencyReport:
    """Article 13 transparency information, serializable for the deployer."""

    system_name: str
    runtime_version: str
    provider_contact: str
    intended_purpose: str
    capabilities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    accuracy_declarations: tuple[AccuracyDeclaration, ...] = ()
    cybersecurity_measures: tuple[str, ...] = ()
    foreseeable_risks: tuple[dict[str, Any], ...] = ()
    human_oversight: HumanOversightConfig | None = None
    logging_mechanism: dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "runtime_version": self.runtime_version,
            "provider_contact": self.provider_contact,
            "intended_purpose": self.intended_purpose,
            "capabilities": list(self.capabilities),
            "limitations": list(self.limitations),
            "accuracy_declarations": [a.to_dict() for a in self.accuracy_declarations],
            "cybersecurity_measures": list(self.cybersecurity_measures),
            "foreseeable_risks": list(self.foreseeable_risks),
            "human_oversight": (
                self.human_oversight.to_dict() if self.human_oversight else None
            ),
            "logging_mechanism": self.logging_mechanism,
            "generated_at": self.generated_at.isoformat(),
        }

    def to_markdown(self) -> str:
        def bullets(items: tuple[str, ...]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "- —"

        lines = [
            f"# Instructions for Use — {self.system_name}",
            "",
            "> ⚠️ **Not legal advice.** Engineering transparency information per "
            "EU AI Act Article 13; the deployer remains responsible for "
            "classification and conformity.",
            "",
            f"- **Provider contact:** {self.provider_contact}",
            f"- **Runtime version:** techrevati-runtime {self.runtime_version}",
            f"- **Intended purpose:** {self.intended_purpose}",
            f"- **Generated at:** {self.generated_at.isoformat()}",
            "",
            "## Capabilities",
            bullets(self.capabilities),
            "",
            "## Limitations",
            bullets(self.limitations),
            "",
            "## Performance (caller-declared accuracy)",
        ]
        if self.accuracy_declarations:
            for decl in self.accuracy_declarations:
                ci = (
                    f" (CI {decl.confidence_interval})"
                    if decl.confidence_interval is not None
                    else ""
                )
                lines.append(
                    f"- {decl.metric_name}: {decl.metric_value}{ci} "
                    f"on {decl.measured_on_dataset} "
                    f"({decl.measured_date.date().isoformat()})"
                )
        else:
            lines.append("- —")
        lines += [
            "",
            "## Cybersecurity measures",
            bullets(self.cybersecurity_measures),
            "",
            "## Human oversight",
        ]
        if self.human_oversight is not None:
            ho = self.human_oversight
            lines += [
                f"- Override enabled: {ho.override_enabled}",
                f"- Reviewer authentication: {ho.reviewer_authentication or '—'}",
                f"- Pause conditions: {', '.join(ho.pause_conditions) or '—'}",
            ]
        else:
            lines.append("- Not configured")
        lines += [
            "",
            "## Logging mechanism (Article 12)",
            f"- {self.logging_mechanism or 'not configured'}",
        ]
        return "\n".join(lines)
