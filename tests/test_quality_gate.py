"""Tests for techrevati.runtime.quality_gate"""

from techrevati.runtime.quality_gate import (
    QualityLevel, QualityGate, QualityGateOutcome,
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
