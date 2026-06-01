"""Tests for techrevati.runtime.usage_tracking"""

import json
import logging
import os
import tempfile

import pytest

from techrevati.runtime.usage_tracking import (
    PRICING_TABLE,
    BudgetExceededError,
    ModelPricing,
    UsageLimitExceededError,
    UsageLimits,
    UsageSnapshot,
    UsageTracker,
    _warned_unpriced_models,
    has_pricing,
    load_pricing_from_file,
    register_pricing,
)


@pytest.fixture(autouse=True)
def _isolate_pricing_table():
    """Snapshot and restore the global PRICING_TABLE around each test."""
    snapshot = dict(PRICING_TABLE)
    warned_snapshot = set(_warned_unpriced_models)
    yield
    PRICING_TABLE.clear()
    PRICING_TABLE.update(snapshot)
    _warned_unpriced_models.clear()
    _warned_unpriced_models.update(warned_snapshot)


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


def test_register_pricing_rejects_blank_model_name():
    with pytest.raises(ValueError, match="model"):
        register_pricing("   ", ModelPricing(1.0, 4.0))


def test_register_pricing_rejects_invalid_pricing_type():
    with pytest.raises(TypeError, match="ModelPricing"):
        register_pricing("model-x", object())  # type: ignore[arg-type]


def test_register_pricing_rejects_invalid_conflict_action_without_partial_write():
    with pytest.raises(ValueError, match="on_conflict"):
        register_pricing(
            "fresh-model",
            ModelPricing(1.0, 4.0),
            on_conflict="replace",  # type: ignore[arg-type]
        )
    assert "fresh-model" not in PRICING_TABLE


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_model_pricing_rejects_invalid_rates(value: float):
    with pytest.raises(ValueError):
        ModelPricing(input_per_million=value, output_per_million=4.0)
    with pytest.raises(ValueError):
        ModelPricing(input_per_million=1.0, output_per_million=value)
    with pytest.raises(ValueError):
        ModelPricing(
            input_per_million=1.0,
            output_per_million=4.0,
            cache_write_per_million=value,
        )


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


def test_load_pricing_from_file_rejects_invalid_model_name():
    payload = {"models": {"": {"input_per_million": 1.0, "output_per_million": 4.0}}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        path = fh.name
    try:
        with pytest.raises(ValueError, match="model"):
            load_pricing_from_file(path)
    finally:
        os.unlink(path)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"models": []},
        {"models": {"model-a": []}},
    ],
)
def test_load_pricing_from_file_rejects_invalid_schema(tmp_path, payload):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TypeError):
        load_pricing_from_file(path)


def test_load_pricing_from_file_rejects_partial_update(tmp_path):
    payload = {
        "models": {
            "valid-model": {"input_per_million": 1.0, "output_per_million": 4.0},
            "broken-model": {"input_per_million": 2.0},
        }
    }
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="missing"):
        load_pricing_from_file(path)

    assert "valid-model" not in PRICING_TABLE
    assert "broken-model" not in PRICING_TABLE


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


def test_usage_tracker_snapshots_usage_on_init_record_and_internal_reads():
    usage = UsageSnapshot(input_tokens=100, output_tokens=50, cache_ttl="5m")
    tracker = UsageTracker(turns=[("priced", usage)])
    tracker.record_turn("priced", usage)

    assert tracker.turns[0][1] == usage
    assert tracker.turns[0][1] is not usage
    assert tracker.turns[1][1] == usage
    assert tracker.turns[1][1] is not usage

    first_snapshot = tracker._snapshot_turns()
    second_snapshot = tracker._snapshot_turns()

    assert first_snapshot == second_snapshot
    assert first_snapshot[0][1] is not tracker.turns[0][1]
    assert first_snapshot[0][1] is not second_snapshot[0][1]


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


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_usage_snapshot_rejects_invalid_token_counts(value):
    with pytest.raises(ValueError):
        UsageSnapshot(input_tokens=value)
    with pytest.raises(ValueError):
        UsageSnapshot(tool_calls=value)


def test_usage_snapshot_rejects_invalid_cache_ttl_type():
    with pytest.raises(ValueError, match="cache_ttl"):
        UsageSnapshot(cache_ttl=60)  # type: ignore[arg-type]


def test_usage_snapshot_rejects_empty_cache_ttl():
    with pytest.raises(ValueError, match="cache_ttl"):
        UsageSnapshot(cache_ttl=" ")
    assert UsageSnapshot(cache_ttl=" 1h ").cache_ttl == "1h"


def test_usage_snapshot_from_dict_rejects_non_dict():
    with pytest.raises(TypeError, match="dictionary"):
        UsageSnapshot.from_dict([])  # type: ignore[arg-type]


def test_usage_limits_rejects_invalid_caps():
    with pytest.raises(ValueError, match="total_tokens_max"):
        UsageLimits(total_tokens_max=-1)
    with pytest.raises(ValueError, match="tool_calls_max"):
        UsageLimits(tool_calls_max=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cost_usd_max"):
        UsageLimits(cost_usd_max=float("nan"))


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


def test_has_pricing_returns_false_for_unregistered_model():
    PRICING_TABLE.clear()
    _warned_unpriced_models.clear()
    assert has_pricing("never-heard-of-it") is False


def test_has_pricing_exact_match():
    register_pricing("model-z", ModelPricing(1.0, 4.0))
    assert has_pricing("model-z") is True
    assert has_pricing("Model-Z") is True


def test_has_pricing_prefix_match():
    register_pricing("family", ModelPricing(3.0, 15.0))
    assert has_pricing("family-20260514") is True


def test_record_turn_warns_once_for_unpriced_model(caplog):
    PRICING_TABLE.clear()
    _warned_unpriced_models.clear()
    tracker = UsageTracker()
    with caplog.at_level(logging.WARNING, logger="techrevati.runtime.usage_tracking"):
        tracker.record_turn("ghost-model", UsageSnapshot(input_tokens=100))
        tracker.record_turn("ghost-model", UsageSnapshot(input_tokens=200))
    warnings = [r for r in caplog.records if "ghost-model" in r.getMessage()]
    assert len(warnings) == 1


def test_record_turn_does_not_warn_for_priced_model(caplog):
    register_pricing("priced", ModelPricing(1.0, 4.0))
    _warned_unpriced_models.clear()
    tracker = UsageTracker()
    with caplog.at_level(logging.WARNING, logger="techrevati.runtime.usage_tracking"):
        tracker.record_turn("priced", UsageSnapshot(input_tokens=100))
    assert not any("no pricing" in r.getMessage() for r in caplog.records)


def test_record_turn_with_empty_model_does_not_warn(caplog):
    """Empty model string is the 'no model' sentinel; don't spam warnings."""
    _warned_unpriced_models.clear()
    tracker = UsageTracker()
    with caplog.at_level(logging.WARNING, logger="techrevati.runtime.usage_tracking"):
        tracker.record_turn("", UsageSnapshot(input_tokens=100))
    assert not any("no pricing" in r.getMessage() for r in caplog.records)


def test_usage_tracker_rejects_invalid_turn_inputs():
    tracker = UsageTracker()
    with pytest.raises(ValueError, match="model"):
        tracker.record_turn(" ", UsageSnapshot())
    with pytest.raises(TypeError, match="UsageSnapshot"):
        tracker.record_turn("m", object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UsageSnapshot"):
        tracker.cost_for_turn("m", object())  # type: ignore[arg-type]


def test_usage_tracker_rejects_invalid_budgets_and_limits():
    tracker = UsageTracker()
    with pytest.raises(ValueError, match="budget_usd"):
        tracker.budget_remaining(float("nan"))
    with pytest.raises(ValueError, match="budget_usd"):
        tracker.is_over_budget(-1.0)
    with pytest.raises(TypeError, match="UsageLimits"):
        tracker.check_limits(object())  # type: ignore[arg-type]


def test_budget_exceeded_error_carries_values():
    err = BudgetExceededError(budget_usd=10.0, current_cost_usd=12.5)
    assert err.budget_usd == 10.0
    assert err.current_cost_usd == 12.5
    assert "10.0000" in str(err)
    assert "12.5000" in str(err)


def test_usage_bound_errors_validate_constructor_inputs():
    with pytest.raises(ValueError, match="budget_usd"):
        BudgetExceededError(budget_usd=-1.0, current_cost_usd=0.0)
    with pytest.raises(ValueError, match="limit_name"):
        UsageLimitExceededError(limit_name="", observed=1, ceiling=0)


def test_resolve_pricing_is_public_exact_prefix_and_zero_fallback():
    # resolve_pricing is now a public export (was private _resolve_pricing).
    from techrevati.runtime import resolve_pricing as public_resolve
    from techrevati.runtime.usage_tracking import _resolve_pricing, resolve_pricing

    assert public_resolve is resolve_pricing
    assert _resolve_pricing is resolve_pricing  # backwards-compatible alias

    register_pricing("dxmodel-x", ModelPricing(1.0, 2.0))
    assert resolve_pricing("dxmodel-x").input_per_million == 1.0  # exact
    # longest-prefix match for a dated variant
    assert resolve_pricing("dxmodel-x-20260601").input_per_million == 1.0
    # zero-fallback for an unknown model — never raises
    assert resolve_pricing("totally-unregistered-zzz") == ModelPricing(0.0, 0.0)
