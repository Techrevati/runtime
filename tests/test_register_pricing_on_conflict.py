"""register_pricing(on_conflict=...) — explicit merge semantics."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    PRICING_TABLE,
    ModelPricing,
    PricingAlreadyRegisteredError,
    register_pricing,
)


@pytest.fixture
def isolated_model_key():
    """Use a unique model name and clean up after the test."""
    model = "test-on-conflict-fixture-model"
    yield model
    PRICING_TABLE.pop(model.lower(), None)


def _pricing(input_rate: float) -> ModelPricing:
    return ModelPricing(input_per_million=input_rate, output_per_million=input_rate)


def test_overwrite_is_default_and_preserves_0_2_0_behavior(isolated_model_key):
    register_pricing(isolated_model_key, _pricing(1.0))
    register_pricing(isolated_model_key, _pricing(2.0))  # default = overwrite
    assert PRICING_TABLE[isolated_model_key].input_per_million == 2.0


def test_explicit_overwrite_matches_default(isolated_model_key):
    register_pricing(isolated_model_key, _pricing(1.0))
    register_pricing(isolated_model_key, _pricing(2.0), on_conflict="overwrite")
    assert PRICING_TABLE[isolated_model_key].input_per_million == 2.0


def test_error_raises_on_existing_key(isolated_model_key):
    register_pricing(isolated_model_key, _pricing(1.0))
    with pytest.raises(PricingAlreadyRegisteredError) as exc_info:
        register_pricing(isolated_model_key, _pricing(2.0), on_conflict="error")
    assert exc_info.value.model == isolated_model_key
    # First-write value retained on error
    assert PRICING_TABLE[isolated_model_key].input_per_million == 1.0


def test_error_passes_through_on_new_key(isolated_model_key):
    # When the key is absent, on_conflict='error' must not raise.
    register_pricing(isolated_model_key, _pricing(3.0), on_conflict="error")
    assert PRICING_TABLE[isolated_model_key].input_per_million == 3.0


def test_keep_drops_new_pricing_silently(isolated_model_key):
    register_pricing(isolated_model_key, _pricing(1.0))
    register_pricing(isolated_model_key, _pricing(2.0), on_conflict="keep")
    # Existing value retained
    assert PRICING_TABLE[isolated_model_key].input_per_million == 1.0


def test_keep_writes_when_key_absent(isolated_model_key):
    register_pricing(isolated_model_key, _pricing(3.0), on_conflict="keep")
    assert PRICING_TABLE[isolated_model_key].input_per_million == 3.0


def test_model_name_is_lowercased(isolated_model_key):
    register_pricing(isolated_model_key.upper(), _pricing(1.0))
    with pytest.raises(PricingAlreadyRegisteredError):
        register_pricing(isolated_model_key, _pricing(2.0), on_conflict="error")
