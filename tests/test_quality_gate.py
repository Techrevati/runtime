"""Tests for techrevati.runtime.quality_gate"""

from typing import Any, cast

import pytest

from techrevati.runtime.quality_gate import (
    QualityGate,
    QualityGateOutcome,
    QualityLevel,
)


def test_level_ordering():
    assert QualityLevel.MINIMAL < QualityLevel.STANDARD
    assert QualityLevel.STANDARD < QualityLevel.STRICT
    assert QualityLevel.STRICT < QualityLevel.RELEASE


def test_matching_level_satisfies():
    gate = QualityGate(QualityLevel.STANDARD)
    outcome = gate.evaluate(QualityLevel.STANDARD)
    assert outcome.satisfied is True


def test_higher_level_satisfies():
    gate = QualityGate(QualityLevel.STANDARD)
    outcome = gate.evaluate(QualityLevel.RELEASE)
    assert outcome.satisfied is True


def test_lower_level_unsatisfied():
    gate = QualityGate(QualityLevel.STRICT)
    outcome = gate.evaluate(QualityLevel.MINIMAL)
    assert outcome.satisfied is False


def test_none_level_unsatisfied():
    gate = QualityGate(QualityLevel.MINIMAL)
    outcome = gate.evaluate(None)
    assert outcome.satisfied is False


def test_gate_accepts_integer_quality_values():
    gate = QualityGate(cast(Any, QualityLevel.STANDARD.value))
    outcome = gate.evaluate(cast(Any, QualityLevel.STRICT.value))
    assert gate.required_level is QualityLevel.STANDARD
    assert outcome.observed_level is QualityLevel.STRICT
    assert outcome.satisfied is True


def test_gate_rejects_invalid_quality_values():
    with pytest.raises(ValueError, match="valid QualityLevel"):
        QualityGate(cast(Any, 99))
    with pytest.raises(TypeError, match="QualityLevel"):
        QualityGate(cast(Any, True))

    gate = QualityGate(QualityLevel.STANDARD)
    with pytest.raises(ValueError, match="valid QualityLevel"):
        gate.evaluate(cast(Any, 99))
    with pytest.raises(TypeError, match="QualityLevel"):
        gate.is_satisfied_by(cast(Any, False))


def test_is_satisfied_by_convenience():
    gate = QualityGate(QualityLevel.STANDARD)
    assert gate.is_satisfied_by(QualityLevel.STRICT) is True
    assert gate.is_satisfied_by(QualityLevel.MINIMAL) is False


def test_label_property():
    assert QualityLevel.MINIMAL.label == "Minimal"
    assert QualityLevel.RELEASE.label == "Release"


def test_outcome_to_dict():
    outcome = QualityGateOutcome(True, QualityLevel.STANDARD, QualityLevel.STRICT)
    d = outcome.to_dict()
    assert d["satisfied"] is True
    assert d["required_level"] == "STANDARD"
    assert d["observed_level"] == "STRICT"


def test_outcome_to_dict_with_none_observed():
    outcome = QualityGateOutcome(False, QualityLevel.STANDARD, None)
    d = outcome.to_dict()
    assert d["satisfied"] is False
    assert d["observed_level"] is None


def test_outcome_rejects_invalid_shape():
    with pytest.raises(TypeError, match="satisfied"):
        QualityGateOutcome(cast(Any, 1), QualityLevel.STANDARD, QualityLevel.STRICT)
    with pytest.raises(ValueError, match="must match"):
        QualityGateOutcome(True, QualityLevel.STRICT, QualityLevel.STANDARD)
