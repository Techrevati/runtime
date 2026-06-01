"""Tests for the risk registry (EU AI Act Article 9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from techrevati.runtime.compliance import (
    ResidualRiskLevel,
    Risk,
    RiskRegistry,
    RiskUnacceptableError,
)


def _risk(rid: str, residual: ResidualRiskLevel = ResidualRiskLevel.LOW) -> Risk:
    return Risk(id=rid, description=f"risk {rid}", residual=residual)


def test_risk_requires_aware_timestamp() -> None:
    with pytest.raises(ValueError):
        Risk(
            id="r1",
            description="d",
            residual=ResidualRiskLevel.LOW,
            last_reviewed=datetime(2026, 1, 1),  # naive
        )


def test_risk_review_due_after_interval() -> None:
    old = datetime.now(UTC) - timedelta(days=200)
    risk = Risk(
        id="r1",
        description="bias",
        residual=ResidualRiskLevel.MEDIUM,
        last_reviewed=old,
        review_interval=timedelta(days=180),
    )
    assert risk.review_due()
    refreshed = risk.reviewed()
    assert not refreshed.review_due()


def test_registry_flags_due_reviews() -> None:
    fresh = _risk("fresh")
    stale = Risk(
        id="stale",
        description="d",
        residual=ResidualRiskLevel.LOW,
        last_reviewed=datetime.now(UTC) - timedelta(days=365),
    )
    reg = RiskRegistry([fresh, stale])
    due = reg.review_due()
    assert [r.id for r in due] == ["stale"]


def test_duplicate_id_rejected() -> None:
    reg = RiskRegistry([_risk("dup")])
    with pytest.raises(ValueError):
        reg.add(_risk("dup"))


def test_assert_no_unacceptable_passes_when_clean() -> None:
    reg = RiskRegistry([_risk("a"), _risk("b", ResidualRiskLevel.HIGH)])
    reg.assert_no_unacceptable()  # no raise


def test_assert_no_unacceptable_raises() -> None:
    reg = RiskRegistry([_risk("ok"), _risk("bad", ResidualRiskLevel.UNACCEPTABLE)])
    with pytest.raises(RiskUnacceptableError) as exc:
        reg.assert_no_unacceptable()
    assert "bad" in str(exc.value)
    assert exc.value.risks[0].id == "bad"


def test_to_dict_roundtrip_shape() -> None:
    reg = RiskRegistry([_risk("a")])
    d = reg.to_dict()
    assert d["risks"][0]["id"] == "a"
    assert d["risks"][0]["residual"] == "low"


def test_mitigation_recipe_recorded() -> None:
    risk = Risk(
        id="timeout",
        description="LLM timeout",
        residual=ResidualRiskLevel.LOW,
        affected_articles=("art.15",),
        mitigation_recipe="LLM_TIMEOUT",
    )
    assert risk.to_dict()["mitigation_recipe"] == "LLM_TIMEOUT"
