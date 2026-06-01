"""
Risk registry — continuous risk management (EU AI Act Article 9).

Article 9 requires a *continuous, iterative* risk-management process over the
lifecycle of a high-risk AI system: identify foreseeable risks, attach mitigation
measures, assess the residual risk, and review periodically.

:class:`RiskRegistry` is a small, declarative primitive for that bookkeeping. It
does not *measure* risk — the deployer declares each :class:`Risk`, points it at a
mitigation (by ``RecoveryRecipe`` scenario name, where applicable), records the
residual level, and the registry flags reviews that have come due and blocks
deployment when any residual risk is ``UNACCEPTABLE`` (Article 9(4)).

.. warning::

    Engineering primitive, not legal advice. Residual-risk levels and review
    cadences are the deployer's determination.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

__all__ = [
    "ResidualRiskLevel",
    "Risk",
    "RiskRegistry",
    "RiskUnacceptableError",
]


class ResidualRiskLevel(str, Enum):
    """Residual risk after mitigation. ``UNACCEPTABLE`` blocks deployment."""

    NEGLIGIBLE = "negligible"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNACCEPTABLE = "unacceptable"


class RiskUnacceptableError(Exception):
    """Raised when a registry contains an ``UNACCEPTABLE`` residual risk."""

    def __init__(self, risks: tuple[Risk, ...]) -> None:
        self.risks = risks
        ids = ", ".join(r.id for r in risks)
        super().__init__(f"unacceptable residual risk(s): {ids}")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Risk:
    """A single declared risk and its mitigation (Article 9)."""

    id: str
    description: str
    residual: ResidualRiskLevel
    affected_articles: tuple[str, ...] = ()
    mitigation_recipe: str | None = None  # RecoveryRecipe scenario name, if any
    last_reviewed: datetime = field(default_factory=_now)
    review_interval: timedelta = timedelta(days=180)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Risk.id must be a non-empty string")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Risk.description must be a non-empty string")
        object.__setattr__(self, "residual", ResidualRiskLevel(self.residual))
        if self.review_interval <= timedelta(0):
            raise ValueError("review_interval must be positive")
        if self.last_reviewed.tzinfo is None:
            raise ValueError("last_reviewed must be timezone-aware (UTC)")

    def review_due(self, *, now: datetime | None = None) -> bool:
        reference = now or _now()
        return reference - self.last_reviewed > self.review_interval

    def reviewed(self, *, at: datetime | None = None) -> Risk:
        """Return a copy with ``last_reviewed`` bumped (review completed)."""
        return replace(self, last_reviewed=at or _now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "residual": self.residual.value,
            "affected_articles": list(self.affected_articles),
            "mitigation_recipe": self.mitigation_recipe,
            "last_reviewed": self.last_reviewed.isoformat(),
            "review_interval_days": self.review_interval.days,
        }


class RiskRegistry:
    """A collection of declared :class:`Risk` entries, keyed by ``id``."""

    def __init__(self, risks: Iterable[Risk] = ()) -> None:
        self._risks: dict[str, Risk] = {}
        for risk in risks:
            self.add(risk)

    def add(self, risk: Risk) -> None:
        if not isinstance(risk, Risk):
            raise TypeError("risk must be a Risk")
        if risk.id in self._risks:
            raise ValueError(f"duplicate risk id: {risk.id!r}")
        self._risks[risk.id] = risk

    def get(self, risk_id: str) -> Risk | None:
        return self._risks.get(risk_id)

    def __len__(self) -> int:
        return len(self._risks)

    def __iter__(self) -> Iterator[Risk]:
        return iter(self._risks.values())

    def review_due(self, *, now: datetime | None = None) -> list[Risk]:
        """Risks whose review interval has elapsed (Article 9 continuous review)."""
        return [r for r in self._risks.values() if r.review_due(now=now)]

    def unacceptable(self) -> tuple[Risk, ...]:
        return tuple(
            r
            for r in self._risks.values()
            if r.residual is ResidualRiskLevel.UNACCEPTABLE
        )

    def assert_no_unacceptable(self) -> None:
        """Raise :class:`RiskUnacceptableError` if any residual risk is unacceptable.

        Article 9(4): residual risks must be judged acceptable before deployment.
        """
        bad = self.unacceptable()
        if bad:
            raise RiskUnacceptableError(bad)

    def to_dict(self) -> dict[str, Any]:
        return {"risks": [r.to_dict() for r in self._risks.values()]}
