"""
Typed outputs — validate raw model text into a typed value.

An ``OutputSpec[T]`` turns the opaque string a model returns into a typed,
validated Python value. The runtime ships three reference implementations:

- :class:`JsonOutputSpec` — parse JSON, optionally enforce required keys / a top
  level type.
- :class:`RegexOutputSpec` — match a regex, return its named groups.
- :class:`CallableOutputSpec` — wrap any ``Callable[[str], T]`` and normalize its
  failures.

Specs are pure and caller-applied — the runtime does not own the model call, so
you parse the result yourself:

    spec = JsonOutputSpec(required_keys=("decision", "reason"))
    raw, usage = session.run_turn(lambda: call_model(prompt))
    parsed = spec.parse(raw)            # OutputValidationError on bad output

Anything that does not satisfy the spec raises :class:`OutputValidationError`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

__all__ = [
    "CallableOutputSpec",
    "JsonOutputSpec",
    "OutputSpec",
    "OutputValidationError",
    "RegexOutputSpec",
]


class OutputValidationError(ValueError):
    """Raised when raw model output does not satisfy an :class:`OutputSpec`."""


@runtime_checkable
class OutputSpec(Protocol[T_co]):
    """Parse raw model output into a typed value (or raise OutputValidationError)."""

    def parse(self, raw: str) -> T_co: ...


@dataclass(frozen=True)
class JsonOutputSpec:
    """Parse JSON output, optionally enforcing required keys and a top-level type.

    ``strip_fences`` removes a leading/trailing Markdown code fence (```` ```json ````)
    that models commonly wrap JSON in.
    """

    required_keys: tuple[str, ...] = ()
    require_type: type | None = None
    strip_fences: bool = True

    def parse(self, raw: str) -> Any:
        if not isinstance(raw, str):
            raise OutputValidationError("output must be a string")
        text = _strip_code_fence(raw) if self.strip_fences else raw
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OutputValidationError(f"invalid JSON: {exc}") from exc
        if self.require_type is not None and not isinstance(value, self.require_type):
            raise OutputValidationError(
                f"expected JSON {self.require_type.__name__}, "
                f"got {type(value).__name__}"
            )
        if self.required_keys:
            if not isinstance(value, dict):
                raise OutputValidationError(
                    "required_keys set but JSON top level is not an object"
                )
            missing = [k for k in self.required_keys if k not in value]
            if missing:
                raise OutputValidationError(f"missing required keys: {missing}")
        return value


@dataclass(frozen=True)
class RegexOutputSpec:
    """Match output against a regex and return its named groups as a dict.

    Uses :meth:`re.Pattern.search` by default; set ``fullmatch=True`` to require
    the whole string to match.
    """

    pattern: str
    flags: int = 0
    fullmatch: bool = False

    def parse(self, raw: str) -> dict[str, str]:
        if not isinstance(raw, str):
            raise OutputValidationError("output must be a string")
        regex = re.compile(self.pattern, self.flags)
        match = regex.fullmatch(raw) if self.fullmatch else regex.search(raw)
        if match is None:
            raise OutputValidationError(
                f"output did not match pattern {self.pattern!r}"
            )
        return {k: v for k, v in match.groupdict().items() if v is not None}


class CallableOutputSpec(Generic[T]):
    """Wrap an arbitrary ``Callable[[str], T]`` as an :class:`OutputSpec`.

    Exceptions raised by the callable are normalized to
    :class:`OutputValidationError` (``OutputValidationError`` itself passes
    through unchanged).
    """

    def __init__(self, fn: Callable[[str], T]) -> None:
        if not callable(fn):
            raise TypeError("fn must be callable")
        self._fn = fn

    def parse(self, raw: str) -> T:
        if not isinstance(raw, str):
            raise OutputValidationError("output must be a string")
        try:
            return self._fn(raw)
        except OutputValidationError:
            raise
        except Exception as exc:
            raise OutputValidationError(f"output validation failed: {exc}") from exc


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2:
        return text
    # Drop the opening fence (``` or ```json) and a trailing fence if present.
    body = lines[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body)
