"""Tests for typed output specs."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    CallableOutputSpec,
    JsonOutputSpec,
    OutputSpec,
    OutputValidationError,
    RegexOutputSpec,
)


def test_json_spec_parses_object() -> None:
    spec = JsonOutputSpec(required_keys=("decision", "reason"))
    value = spec.parse('{"decision": "approve", "reason": "ok"}')
    assert value == {"decision": "approve", "reason": "ok"}


def test_json_spec_strips_code_fence() -> None:
    spec = JsonOutputSpec()
    assert spec.parse('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_spec_missing_keys_raises() -> None:
    spec = JsonOutputSpec(required_keys=("decision",))
    with pytest.raises(OutputValidationError) as exc:
        spec.parse('{"reason": "x"}')
    assert "missing required keys" in str(exc.value)


def test_json_spec_invalid_json_raises() -> None:
    with pytest.raises(OutputValidationError):
        JsonOutputSpec().parse("not json")


def test_json_spec_require_type() -> None:
    spec = JsonOutputSpec(require_type=list)
    assert spec.parse("[1, 2, 3]") == [1, 2, 3]
    with pytest.raises(OutputValidationError):
        spec.parse('{"a": 1}')


def test_json_spec_required_keys_non_object_raises() -> None:
    spec = JsonOutputSpec(required_keys=("x",))
    with pytest.raises(OutputValidationError):
        spec.parse("[1, 2]")


def test_regex_spec_named_groups() -> None:
    spec = RegexOutputSpec(r"score=(?P<score>\d+)")
    assert spec.parse("the score=42 result") == {"score": "42"}


def test_regex_spec_no_match_raises() -> None:
    spec = RegexOutputSpec(r"\d+", fullmatch=True)
    with pytest.raises(OutputValidationError):
        spec.parse("abc")


def test_callable_spec_wraps_value() -> None:
    spec = CallableOutputSpec(lambda raw: int(raw.strip()))
    assert spec.parse("  7 ") == 7


def test_callable_spec_normalizes_exceptions() -> None:
    spec = CallableOutputSpec(lambda raw: int(raw))
    with pytest.raises(OutputValidationError):
        spec.parse("notanumber")


def test_callable_spec_passes_through_validation_error() -> None:
    def fn(raw: str) -> str:
        raise OutputValidationError("custom")

    with pytest.raises(OutputValidationError, match="custom"):
        CallableOutputSpec(fn).parse("x")


def test_specs_satisfy_protocol() -> None:
    assert isinstance(JsonOutputSpec(), OutputSpec)
    assert isinstance(RegexOutputSpec(r"."), OutputSpec)
    assert isinstance(CallableOutputSpec(str), OutputSpec)


def test_non_string_input_raises() -> None:
    for spec in (JsonOutputSpec(), RegexOutputSpec(r"."), CallableOutputSpec(str)):
        with pytest.raises(OutputValidationError):
            spec.parse(123)  # type: ignore[arg-type]
