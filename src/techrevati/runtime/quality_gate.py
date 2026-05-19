"""
Quality Gate — Graduated quality levels for pass/fail decisions.

Replaces binary pass/fail with a four-level classification. `QualityLevel`
is an IntEnum so `>=` comparison works naturally. Callers define their own
mapping from observed metrics (confidence scores, test counts, signal
strength) to a `QualityLevel`; the runtime stays opinion-free about what
those numbers mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class QualityLevel(IntEnum):
    """Ordered quality levels. Higher is stricter."""

    MINIMAL = 1
    STANDARD = 2
    STRICT = 3
    RELEASE = 4

    @property
    def label(self) -> str:
        return self.name.title()


@dataclass(frozen=True)
class QualityGateOutcome:
    """Result of evaluating a QualityGate."""

    satisfied: bool
    required_level: QualityLevel
    observed_level: QualityLevel | None

    def is_satisfied(self) -> bool:
        return self.satisfied

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "required_level": self.required_level.name,
            "observed_level": self.observed_level.name if self.observed_level else None,
        }


@dataclass(frozen=True)
class QualityGate:
    """Gate requiring a minimum QualityLevel."""

    required_level: QualityLevel

    def evaluate(self, observed: QualityLevel | None) -> QualityGateOutcome:
        satisfied = observed is not None and observed >= self.required_level
        return QualityGateOutcome(
            satisfied=satisfied,
            required_level=self.required_level,
            observed_level=observed,
        )

    def is_satisfied_by(self, observed: QualityLevel) -> bool:
        return observed >= self.required_level
