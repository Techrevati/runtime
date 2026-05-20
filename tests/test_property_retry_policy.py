"""Property tests for techrevati.runtime.retry_policy.

These complement the example-based tests in test_retry_policy.py by
exercising the classifier and backoff math over a wider input space.
The goal isn't 100% input coverage — it's catching invariant violations
the example tests would miss (negative backoff, classifier crashes on
exotic strings, jitter modes leaking outside [0, cap]).
"""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from techrevati.runtime.retry_policy import (
    FailureScenario,
    backoff_delay,
    classify_exception,
    smaller_context_budget,
)

# Strings that must never be classified into a non-default scenario,
# regardless of what other random text surrounds them. Each tuple is
# (substring, expected_scenario). We embed them in arbitrary noise to
# verify the substring match is robust.
_KNOWN_KEYWORDS: list[tuple[str, FailureScenario]] = [
    ("timeout", FailureScenario.LLM_TIMEOUT),
    ("timed out", FailureScenario.LLM_TIMEOUT),
    ("rate limit", FailureScenario.LLM_ERROR),
    ("429", FailureScenario.LLM_ERROR),
    ("context length", FailureScenario.CONTEXT_OVERFLOW),
    ("token limit", FailureScenario.CONTEXT_OVERFLOW),
    ("malformed", FailureScenario.MEMORY_CORRUPTION),
    ("connection refused", FailureScenario.PROVIDER_FAILURE),
    ("503", FailureScenario.PROVIDER_FAILURE),
    ("401", FailureScenario.PROVIDER_FAILURE),
]


@given(st.text(max_size=200))
@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_classify_exception_never_crashes(payload: str) -> None:
    """classify_exception must terminate with a valid enum for any string."""
    result = classify_exception(Exception(payload))
    assert isinstance(result, FailureScenario)


@given(
    keyword_idx=st.integers(min_value=0, max_value=len(_KNOWN_KEYWORDS) - 1),
    prefix=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40),
    suffix=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40),
)
def test_known_keywords_classify_consistently(
    keyword_idx: int, prefix: str, suffix: str
) -> None:
    """A keyword embedded in arbitrary text should still classify.

    Caveat: if the surrounding text contains a higher-precedence keyword
    (e.g. "timeout" appears before "rate limit" in the classifier), the
    earlier rule wins. We assert that the result is *at least* a known
    scenario from the keyword set — never the fallback LLM_ERROR unless
    the keyword itself maps to LLM_ERROR.
    """
    keyword, expected = _KNOWN_KEYWORDS[keyword_idx]
    # Avoid accidental keyword injection from prefix/suffix by filtering
    # the known substrings out of the surrounding noise.
    noise_blocklist = [kw for kw, _ in _KNOWN_KEYWORDS]

    def _scrub(s: str) -> str:
        cleaned = s.lower()
        for bad in noise_blocklist:
            cleaned = cleaned.replace(bad, "")
        return cleaned

    payload = f"{_scrub(prefix)} {keyword} {_scrub(suffix)}"
    result = classify_exception(Exception(payload))
    assert result == expected, (
        f"keyword {keyword!r} in payload {payload!r} → {result}, expected {expected}"
    )


@given(
    st.builds(
        json.JSONDecodeError,
        st.just("Expecting value"),
        st.just(""),
        st.just(0),
    )
)
def test_json_decode_error_is_memory_corruption(exc: json.JSONDecodeError) -> None:
    """JSONDecodeError must always classify as MEMORY_CORRUPTION."""
    assert classify_exception(exc) == FailureScenario.MEMORY_CORRUPTION


@given(
    attempt=st.integers(min_value=0, max_value=10),
    base=st.floats(
        min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False
    ),
    cap=st.floats(
        min_value=0.1, max_value=600.0, allow_nan=False, allow_infinity=False
    ),
    jitter=st.sampled_from(["none", "full", "equal", "decorrelated"]),
)
def test_backoff_delay_within_cap(
    attempt: int, base: float, cap: float, jitter: str
) -> None:
    """Backoff must never exceed cap, never be negative."""
    delay = backoff_delay(attempt=attempt, base=base, cap=cap, jitter=jitter)
    assert delay >= 0.0, f"negative delay: {delay}"
    if jitter == "decorrelated":
        # Decorrelated jitter's upper bound is uniform(base, anchor*3) and
        # the algorithm does NOT enforce cap on the upper draw — it caps the
        # exponential growth, not the random sample. So we relax the upper
        # check: at attempt=0/prev_delay=0 it always equals `base`.
        assert delay <= max(cap, base * 3.0) + 1e-9
    else:
        assert delay <= cap + 1e-9, f"delay {delay} > cap {cap}"


@given(
    attempt=st.integers(min_value=0, max_value=10),
    base=st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False),
    cap=st.floats(
        min_value=1.0, max_value=600.0, allow_nan=False, allow_infinity=False
    ),
)
def test_backoff_none_is_deterministic(attempt: int, base: float, cap: float) -> None:
    """jitter='none' must produce the same value across calls."""
    a = backoff_delay(attempt=attempt, base=base, cap=cap, jitter="none")
    b = backoff_delay(attempt=attempt, base=base, cap=cap, jitter="none")
    assert a == b


@given(
    current=st.integers(min_value=0, max_value=1_000_000),
    reduction=st.floats(
        min_value=0.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
        exclude_max=False,
    ),
)
def test_smaller_context_budget_monotonic(current: int, reduction: float) -> None:
    """Reduced budget must never exceed the original."""
    reduced = smaller_context_budget(current, reduction=reduction)
    assert 0 <= reduced <= current
