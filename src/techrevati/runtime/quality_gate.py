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


def _coerce_quality_level(
    field_name: str,
    value: QualityLevel | int | None,
    *,
    allow_none: bool = False,
) -> QualityLevel | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{field_name} is required")
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a QualityLevel")
    if isinstance(value, QualityLevel):
        return value
    try:
        return QualityLevel(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid QualityLevel") from exc
    except TypeError as exc:
        raise TypeError(f"{field_name} must be a QualityLevel") from exc


@dataclass(frozen=True)
class QualityGateOutcome:
    """Result of evaluating a QualityGate."""

    satisfied: bool
    required_level: QualityLevel
    observed_level: QualityLevel | None

    def __post_init__(self) -> None:
        if not isinstance(self.satisfied, bool):
            raise TypeError("satisfied must be a bool")
        required_level = _coerce_quality_level("required_level", self.required_level)
        assert required_level is not None
        observed_level = _coerce_quality_level(
            "observed_level", self.observed_level, allow_none=True
        )
        expected = observed_level is not None and observed_level >= required_level
        if self.satisfied != expected:
            raise ValueError("satisfied must match required_level and observed_level")
        object.__setattr__(self, "required_level", required_level)
        object.__setattr__(self, "observed_level", observed_level)

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

    def __post_init__(self) -> None:
        required_level = _coerce_quality_level("required_level", self.required_level)
        assert required_level is not None
        object.__setattr__(self, "required_level", required_level)

    def evaluate(self, observed: QualityLevel | int | None) -> QualityGateOutcome:
        observed_level = _coerce_quality_level("observed", observed, allow_none=True)
        satisfied = observed_level is not None and observed_level >= self.required_level
        return QualityGateOutcome(
            satisfied=satisfied,
            required_level=self.required_level,
            observed_level=observed_level,
        )

    def is_satisfied_by(self, observed: QualityLevel | int) -> bool:
        observed_level = _coerce_quality_level("observed", observed)
        assert observed_level is not None
        return observed_level >= self.required_level
