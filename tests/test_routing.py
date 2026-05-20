"""Tests for techrevati.runtime.routing."""

from __future__ import annotations

from techrevati.runtime import (
    FailureScenario,
    ProviderRouter,
    RoundRobinProviderRouter,
    StaticProviderRouter,
    WeightedProviderRouter,
)

_SCN = FailureScenario.PROVIDER_FAILURE


def test_static_router_returns_first_acceptable() -> None:
    r = StaticProviderRouter(("a", "b", "c"))
    assert r.select(scenario=_SCN, attempt=1, current="a") == "b"


def test_static_router_skips_excluded() -> None:
    r = StaticProviderRouter(("a", "b", "c"))
    assert r.select(scenario=_SCN, attempt=1, current=None, exclude=["a", "b"]) == "c"


def test_static_router_returns_none_when_nothing_left() -> None:
    r = StaticProviderRouter(("a",))
    assert r.select(scenario=_SCN, attempt=1, current="a") is None


def test_static_router_satisfies_protocol() -> None:
    r: ProviderRouter = StaticProviderRouter(("a",))
    assert isinstance(r, ProviderRouter)


def test_round_robin_advances_cursor() -> None:
    r = RoundRobinProviderRouter(("a", "b", "c"))
    assert r.select(scenario=_SCN, attempt=1, current=None) == "a"
    assert r.select(scenario=_SCN, attempt=1, current=None) == "b"
    assert r.select(scenario=_SCN, attempt=1, current=None) == "c"
    # Wraps.
    assert r.select(scenario=_SCN, attempt=1, current=None) == "a"


def test_round_robin_skips_current_and_excluded() -> None:
    r = RoundRobinProviderRouter(("a", "b", "c"))
    # cursor=0; "a" is excluded as current; "b" wins; cursor advances to 2.
    assert r.select(scenario=_SCN, attempt=1, current="a") == "b"
    # cursor=2; with "c" excluded, the next acceptable starting at idx 2 is
    # "a" (the loop wraps). Cursor advances to 1.
    assert r.select(scenario=_SCN, attempt=1, current=None, exclude=["c"]) == "a"
    # cursor=1; nothing excluded → "b".
    assert r.select(scenario=_SCN, attempt=1, current=None) == "b"


def test_round_robin_returns_none_on_empty_list() -> None:
    r = RoundRobinProviderRouter(())
    assert r.select(scenario=_SCN, attempt=1, current=None) is None


def test_weighted_picks_highest_weight() -> None:
    r = WeightedProviderRouter((("a", 1.0), ("b", 5.0), ("c", 2.0)))
    assert r.select(scenario=_SCN, attempt=1, current=None) == "b"


def test_weighted_breaks_ties_by_declaration_order() -> None:
    r = WeightedProviderRouter((("a", 3.0), ("b", 3.0)))
    assert r.select(scenario=_SCN, attempt=1, current=None) == "a"


def test_weighted_skips_zero_or_negative_weight() -> None:
    r = WeightedProviderRouter((("a", 0.0), ("b", -1.0), ("c", 1.0)))
    assert r.select(scenario=_SCN, attempt=1, current=None) == "c"


def test_weighted_returns_none_when_all_excluded() -> None:
    r = WeightedProviderRouter((("a", 1.0), ("b", 2.0)))
    assert r.select(scenario=_SCN, attempt=1, current="a", exclude=["b"]) is None
