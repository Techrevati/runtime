"""Tests for techrevati.runtime.routing."""

from __future__ import annotations

from typing import Any, cast

import pytest

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


def test_static_router_normalizes_provider_names() -> None:
    r = StaticProviderRouter((" a ", " b "))

    assert r.available_providers == ("a", "b")
    assert r.select(scenario=_SCN, attempt=1, current=" a ") == "b"


def test_static_router_skips_excluded() -> None:
    r = StaticProviderRouter(("a", "b", "c"))
    assert r.select(scenario=_SCN, attempt=1, current=None, exclude=["a", "b"]) == "c"


def test_static_router_returns_none_when_nothing_left() -> None:
    r = StaticProviderRouter(("a",))
    assert r.select(scenario=_SCN, attempt=1, current="a") is None


def test_static_router_returns_none_when_all_excluded() -> None:
    r = StaticProviderRouter(("a", "b"))
    assert r.select(scenario=_SCN, attempt=1, current=None, exclude=["a", "b"]) is None


def test_static_router_satisfies_protocol() -> None:
    r: ProviderRouter = StaticProviderRouter(("a",))
    assert isinstance(r, ProviderRouter)


def test_static_router_rejects_invalid_provider_config() -> None:
    with pytest.raises(TypeError, match="sequence"):
        StaticProviderRouter(cast(Any, "a"))
    with pytest.raises(TypeError, match="strings"):
        StaticProviderRouter(cast(Any, ("a", 1)))
    with pytest.raises(ValueError, match="empty"):
        StaticProviderRouter(("a", " "))
    with pytest.raises(ValueError, match="duplicate"):
        StaticProviderRouter(("a", "a"))
    with pytest.raises(ValueError, match="duplicate"):
        StaticProviderRouter(("a", " a "))


@pytest.mark.parametrize(
    ("current", "exclude", "attempt"),
    [
        ("", (), 1),
        (cast(Any, object()), (), 1),
        (None, "a", 1),
        (None, (["a"],), 1),
        (None, (), 0),
        (None, (), cast(Any, True)),
    ],
)
def test_static_router_select_is_fail_safe_for_invalid_runtime_inputs(
    current: str | None,
    exclude: Any,
    attempt: int,
) -> None:
    r = StaticProviderRouter(("a", "b"))

    assert (
        r.select(
            scenario=_SCN,
            attempt=attempt,
            current=current,
            exclude=exclude,
        )
        is None
    )


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


def test_round_robin_rejects_invalid_provider_config() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        RoundRobinProviderRouter(("a", "a"))


def test_round_robin_select_is_fail_safe_for_invalid_runtime_inputs() -> None:
    r = RoundRobinProviderRouter(("a", "b"))

    assert (
        r.select(
            scenario=_SCN,
            attempt=1,
            current=None,
            exclude=cast(Any, "a"),
        )
        is None
    )
    assert r.select(scenario=_SCN, attempt=0, current=None) is None


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


def test_weighted_rejects_invalid_provider_config() -> None:
    with pytest.raises(TypeError, match="pairs"):
        WeightedProviderRouter(cast(Any, ("a",)))
    with pytest.raises(TypeError, match="numbers"):
        WeightedProviderRouter(cast(Any, (("a", "heavy"),)))
    with pytest.raises(TypeError, match="numbers"):
        WeightedProviderRouter(cast(Any, (("a", True),)))
    with pytest.raises(ValueError, match="finite"):
        WeightedProviderRouter((("a", float("nan")),))
    with pytest.raises(ValueError, match="finite"):
        WeightedProviderRouter((("a", float("inf")),))
    with pytest.raises(ValueError, match="duplicate"):
        WeightedProviderRouter((("a", 1.0), ("a", 2.0)))
    with pytest.raises(ValueError, match="empty"):
        WeightedProviderRouter((("", 1.0),))


def test_weighted_normalizes_provider_names_and_select_runtime_inputs() -> None:
    r = WeightedProviderRouter(((" a ", 1.0), (" b ", 2.0)))

    assert r.providers == (("a", 1.0), ("b", 2.0))
    assert r.select(scenario=_SCN, attempt=1, current=" b ") == "a"
    assert (
        r.select(
            scenario=_SCN,
            attempt=1,
            current=None,
            exclude=[" b "],
        )
        == "a"
    )
    assert r.select(scenario=_SCN, attempt=cast(Any, False), current=None) is None
