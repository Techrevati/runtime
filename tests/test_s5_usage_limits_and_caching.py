"""Tests for Sprint 5 additions:

- ``UsageLimits`` per-dimension caps and ``UsageLimitExceededError``
- Ephemeral prompt-caching TTL tiers (5m / 1h) on ``ModelPricing``
- ``UsageBoundExceededError`` shared base class for cost + limit overruns
"""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    AgentSession,
    BudgetExceededError,
    ModelPricing,
    UsageBoundExceededError,
    UsageLimitExceededError,
    UsageLimits,
    UsageSnapshot,
    UsageTracker,
    register_pricing,
)
from techrevati.runtime.usage_tracking import PRICING_TABLE

# --------------------------------------------------------------------------
# Prompt-caching TTL tiers
# --------------------------------------------------------------------------


def test_model_pricing_write_rate_falls_back_when_ttl_unset() -> None:
    p = ModelPricing(
        input_per_million=3.0,
        output_per_million=15.0,
        cache_write_per_million=3.75,
        cache_write_5min_per_million=3.75,
        cache_write_1h_per_million=6.0,
    )
    assert p.write_rate_for_ttl(None) == 3.75
    assert p.write_rate_for_ttl("5m") == 3.75
    assert p.write_rate_for_ttl("1h") == 6.0
    # Unknown TTL hint falls back to legacy single-tier.
    assert p.write_rate_for_ttl("17min") == 3.75


def test_cost_for_turn_applies_1h_cache_write_multiplier() -> None:
    register_pricing(
        "ttl-model",
        ModelPricing(
            input_per_million=3.0,
            output_per_million=15.0,
            cache_write_per_million=3.75,
            cache_write_5min_per_million=3.75,
            cache_write_1h_per_million=6.0,
        ),
    )
    try:
        tracker = UsageTracker()
        # 1M cache-write tokens at 1h TTL: 6.0 USD/M = $6.00.
        cost = tracker.cost_for_turn(
            "ttl-model",
            UsageSnapshot(cache_write_tokens=1_000_000, cache_ttl="1h"),
        )
        assert cost == pytest.approx(6.0)
        # 5m TTL → 3.75 USD/M = $3.75.
        cost_5m = tracker.cost_for_turn(
            "ttl-model",
            UsageSnapshot(cache_write_tokens=1_000_000, cache_ttl="5m"),
        )
        assert cost_5m == pytest.approx(3.75)
    finally:
        PRICING_TABLE.pop("ttl-model", None)


# --------------------------------------------------------------------------
# UsageLimits
# --------------------------------------------------------------------------


def test_check_limits_request_tokens_raises_when_over() -> None:
    tracker = UsageTracker()
    tracker.record_turn("m", UsageSnapshot(input_tokens=5_000, output_tokens=100))
    limits = UsageLimits(request_tokens_max=4_000)
    with pytest.raises(UsageLimitExceededError) as ei:
        tracker.check_limits(limits)
    assert ei.value.limit_name == "request_tokens"


def test_check_limits_response_tokens() -> None:
    tracker = UsageTracker()
    tracker.record_turn("m", UsageSnapshot(input_tokens=100, output_tokens=999))
    with pytest.raises(UsageLimitExceededError) as ei:
        tracker.check_limits(UsageLimits(response_tokens_max=500))
    assert ei.value.limit_name == "response_tokens"


def test_check_limits_total_tokens() -> None:
    tracker = UsageTracker()
    tracker.record_turn("m", UsageSnapshot(input_tokens=600, output_tokens=600))
    with pytest.raises(UsageLimitExceededError) as ei:
        tracker.check_limits(UsageLimits(total_tokens_max=1_000))
    assert ei.value.limit_name == "total_tokens"


def test_check_limits_tool_calls() -> None:
    tracker = UsageTracker()
    tracker.record_turn("m", UsageSnapshot(tool_calls=5))
    tracker.record_turn("m", UsageSnapshot(tool_calls=2))
    with pytest.raises(UsageLimitExceededError) as ei:
        tracker.check_limits(UsageLimits(tool_calls_max=6))
    assert ei.value.limit_name == "tool_calls"


def test_check_limits_cost_usd_max() -> None:
    register_pricing(
        "priced", ModelPricing(input_per_million=10.0, output_per_million=20.0)
    )
    try:
        tracker = UsageTracker()
        tracker.record_turn(
            "priced", UsageSnapshot(input_tokens=1_000_000, output_tokens=0)
        )
        # 1M * 10 / 1M = $10.00; cap $5 → over.
        with pytest.raises(UsageLimitExceededError) as ei:
            tracker.check_limits(UsageLimits(cost_usd_max=5.0))
        assert ei.value.limit_name == "cost_usd"
    finally:
        PRICING_TABLE.pop("priced", None)


def test_check_limits_no_raise_when_under() -> None:
    tracker = UsageTracker()
    tracker.record_turn(
        "m", UsageSnapshot(input_tokens=10, output_tokens=10, tool_calls=1)
    )
    tracker.check_limits(UsageLimits(request_tokens_max=100, tool_calls_max=10))


def test_unconfigured_limits_is_noop() -> None:
    tracker = UsageTracker()
    tracker.record_turn("m", UsageSnapshot(input_tokens=1_000_000))
    # All None → never raises.
    tracker.check_limits(UsageLimits())


# --------------------------------------------------------------------------
# UsageBoundExceededError as a unified catch
# --------------------------------------------------------------------------


def test_budget_and_limit_share_base_class() -> None:
    assert issubclass(BudgetExceededError, UsageBoundExceededError)
    assert issubclass(UsageLimitExceededError, UsageBoundExceededError)


# --------------------------------------------------------------------------
# Orchestrator integration
# --------------------------------------------------------------------------


def test_agent_session_raises_usage_limit_exceeded_in_run_turn() -> None:
    orch = AgentSession(
        role="writer",
        phase="draft",
        usage_limits=UsageLimits(total_tokens_max=100),
    )

    with orch.session() as session:
        session.run_turn(
            lambda: "ok",
            model="m",
            usage=UsageSnapshot(input_tokens=60, output_tokens=20),
        )
        with pytest.raises(UsageLimitExceededError) as ei:
            session.run_turn(
                lambda: "ok-2",
                model="m",
                usage=UsageSnapshot(input_tokens=50, output_tokens=50),
            )
    assert ei.value.limit_name == "total_tokens"
