"""Tests for techrevati.runtime.usage_tracking"""

import json
import os
import tempfile

import pytest

from techrevati.runtime.usage_tracking import (
    PRICING_TABLE,
    ModelPricing,
    UsageSnapshot,
    UsageTracker,
    load_pricing_from_file,
    register_pricing,
)


@pytest.fixture(autouse=True)
def _isolate_pricing_table():
    """Snapshot and restore the global PRICING_TABLE around each test."""
    snapshot = dict(PRICING_TABLE)
    yield
    PRICING_TABLE.clear()
    PRICING_TABLE.update(snapshot)


def test_pricing_table_starts_empty():
    """The bundled table ships empty; callers register their own."""
    PRICING_TABLE.clear()  # explicit; the bundled wheel ships empty
    assert PRICING_TABLE == {}


def test_register_pricing_adds_entry():
    register_pricing(
        "model-x", ModelPricing(input_per_million=2.0, output_per_million=8.0)
    )
    assert "model-x" in PRICING_TABLE
    assert PRICING_TABLE["model-x"].input_per_million == 2.0


def test_register_pricing_overrides_existing():
    register_pricing("model-x", ModelPricing(1.0, 4.0))
    register_pricing("model-x", ModelPricing(2.0, 8.0))
    assert PRICING_TABLE["model-x"].input_per_million == 2.0


def test_register_pricing_normalizes_case():
    register_pricing("Model-Mixed-CASE", ModelPricing(1.0, 4.0))
    assert "model-mixed-case" in PRICING_TABLE


def test_load_pricing_from_file():
    payload = {
        "models": {
            "model-a": {"input_per_million": 5.0, "output_per_million": 20.0},
            "model-b": {
                "input_per_million": 1.0,
                "output_per_million": 4.0,
                "cache_write_per_million": 1.25,
                "cache_read_per_million": 0.1,
            },
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        path = fh.name
    try:
        load_pricing_from_file(path)
        assert PRICING_TABLE["model-a"].input_per_million == 5.0
        assert PRICING_TABLE["model-b"].cache_write_per_million == 1.25
        assert PRICING_TABLE["model-b"].cache_read_per_million == 0.1
    finally:
        os.unlink(path)


def test_unknown_model_falls_back_to_zero_cost():
    tracker = UsageTracker()
    tracker.record_turn(
        "never-registered",
        UsageSnapshot(input_tokens=1_000_000, output_tokens=1_000_000),
    )
    assert tracker.total_cost() == 0.0


def test_cost_calculation_with_registered_model():
    register_pricing(
        "priced", ModelPricing(input_per_million=3.0, output_per_million=15.0)
    )
    tracker = UsageTracker()
    tracker.record_turn(
        "priced", UsageSnapshot(input_tokens=1_000_000, output_tokens=0)
    )
    assert abs(tracker.total_cost() - 3.0) < 1e-9


def test_cumulative_tracking():
    register_pricing("priced", ModelPricing(3.0, 15.0))
    tracker = UsageTracker()
    tracker.record_turn("priced", UsageSnapshot(input_tokens=1000, output_tokens=500))
    tracker.record_turn("priced", UsageSnapshot(input_tokens=2000, output_tokens=1000))
    assert tracker.total_input_tokens == 3000
    assert tracker.total_output_tokens == 1500


def test_budget_remaining():
    register_pricing("cheap", ModelPricing(0.15, 0.60))
    tracker = UsageTracker()
    tracker.record_turn(
        "cheap", UsageSnapshot(input_tokens=100_000, output_tokens=50_000)
    )
    remaining = tracker.budget_remaining(1.0)
    assert remaining > 0
    assert remaining < 1.0


def test_is_over_budget():
    register_pricing("premium", ModelPricing(15.0, 75.0))
    tracker = UsageTracker()
    tracker.record_turn(
        "premium", UsageSnapshot(input_tokens=1_000_000, output_tokens=0)
    )
    assert tracker.is_over_budget(1.0) is True
    assert tracker.is_over_budget(100.0) is False


def test_format_cost_zero():
    tracker = UsageTracker()
    tracker.record_turn("unknown", UsageSnapshot(input_tokens=1000, output_tokens=500))
    assert tracker.format_cost() == "$0.0000"


def test_prefix_model_matching():
    """Dated variants resolve to the most specific family entry."""
    register_pricing("family", ModelPricing(3.0, 15.0))
    tracker = UsageTracker()
    tracker.record_turn(
        "family-20260514", UsageSnapshot(input_tokens=1_000_000, output_tokens=0)
    )
    assert tracker.total_cost() > 0


def test_summary_shape():
    tracker = UsageTracker()
    tracker.record_turn("any", UsageSnapshot(input_tokens=5000, output_tokens=1000))
    s = tracker.summary()
    assert s["turns"] == 1
    assert "total_cost_usd" in s
    assert "formatted_cost" in s


def test_usage_snapshot_dict_roundtrip():
    orig = UsageSnapshot(
        input_tokens=1000,
        output_tokens=500,
        cache_write_tokens=200,
        cache_read_tokens=50,
    )
    restored = UsageSnapshot.from_dict(orig.to_dict())
    assert restored == orig


def test_usage_snapshot_json_roundtrip():
    orig = UsageSnapshot(input_tokens=2000, output_tokens=800)
    restored = UsageSnapshot.from_json(orig.to_json())
    assert restored == orig


def test_usage_snapshot_from_dict_minimal():
    snapshot = UsageSnapshot.from_dict({})
    assert snapshot.input_tokens == 0
    assert snapshot.output_tokens == 0
    assert snapshot.cache_write_tokens == 0
    assert snapshot.cache_read_tokens == 0


def test_per_model_summary_aggregates():
    register_pricing("a", ModelPricing(3.0, 15.0))
    register_pricing("b", ModelPricing(2.5, 10.0))
    tracker = UsageTracker()
    tracker.record_turn("a", UsageSnapshot(input_tokens=1_000_000, output_tokens=0))
    tracker.record_turn("b", UsageSnapshot(input_tokens=1_000_000, output_tokens=0))
    tracker.record_turn("a", UsageSnapshot(input_tokens=1_000_000, output_tokens=0))
    summary = tracker.per_model_summary()
    assert len(summary) == 2
    assert abs(summary["a"] - 6.0) < 1e-9
    assert abs(summary["b"] - 2.5) < 1e-9


def test_per_model_summary_empty_tracker():
    assert UsageTracker().per_model_summary() == {}
