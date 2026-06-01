"""
Provider routing — Pluggable strategy for picking the next LLM provider.

The runtime already knows *that* a provider failed (``classify_exception``
maps the exception to ``FailureScenario.PROVIDER_FAILURE`` or
``LLM_ERROR`` with a ``SWITCH_PROVIDER`` step in the recipe). What it
does not know on its own is *which* provider to try next when the
caller has a stable list of options. ``ProviderRouter`` is the
extension point.

Three reference implementations cover the common patterns:

- ``StaticProviderRouter`` — wraps the existing ``next_provider``
  function: cycle through ``available_providers`` skipping ones the
  caller has already excluded. Zero-config and stateless.
- ``RoundRobinProviderRouter`` — strictly rotates through the list,
  regardless of which provider failed. Good for cost balancing.
- ``WeightedProviderRouter`` — picks the first provider whose weight
  is non-zero, in declaration order. Deterministic; pair with a
  cron / config reload to express "prefer A, fall back to B at 9pm".

All three are sync. Async sessions call them synchronously (selecting a
provider name is cheap); if a future implementation needs to talk to a
control plane, wrap it in a thread executor in the caller, not here.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from techrevati.runtime.retry_policy import FailureScenario

__all__ = [
    "ProviderRouter",
    "RoundRobinProviderRouter",
    "StaticProviderRouter",
    "WeightedProviderRouter",
]


@runtime_checkable
class ProviderRouter(Protocol):
    """Strategy that picks the next provider to try on a failure step.

    Implementations may be stateful (``RoundRobinProviderRouter`` keeps
    a cursor) or stateless. They must never raise — return ``None``
    when there is no acceptable next provider so the caller can
    escalate.
    """

    def select(
        self,
        *,
        scenario: FailureScenario,
        attempt: int,
        current: str | None,
        exclude: Sequence[str] = (),
    ) -> str | None:
        """Return the next provider name, or ``None`` if none qualifies.

        Parameters
        ----------
        scenario:
            The failure that triggered the switch. Implementations may
            ignore it; useful for "only fail over on PROVIDER_FAILURE"
            policies.
        attempt:
            Which switch attempt this is within the current recovery
            run (1-indexed). Lets weighted routers skip already-tried
            options.
        current:
            The provider the failing call used; routers typically
            avoid returning the same one.
        exclude:
            Additional providers the caller wants skipped (e.g. ones
            that already 429'd this minute).
        """
        ...


def _validate_provider_name(field_name: str, name: str) -> str:
    if not isinstance(name, str):
        raise TypeError(f"{field_name} must contain provider names as strings")
    if not name.strip():
        raise ValueError(f"{field_name} must not contain empty provider names")
    return name.strip()


def _validate_attempt(attempt: int) -> int:
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise TypeError("attempt must be a positive integer")
    if attempt <= 0:
        raise ValueError("attempt must be a positive integer")
    return attempt


def _normalize_provider_names(
    field_name: str,
    providers: Sequence[str],
) -> tuple[str, ...]:
    if isinstance(providers, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence of provider names")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in providers:
        name = _validate_provider_name(field_name, raw_name)
        if name in seen:
            raise ValueError(f"{field_name} must not contain duplicate providers")
        seen.add(name)
        normalized.append(name)
    return tuple(normalized)


def _normalize_weighted_providers(
    providers: Sequence[tuple[str, float]],
) -> tuple[tuple[str, float], ...]:
    if isinstance(providers, (str, bytes)):
        raise TypeError("providers must be a sequence of (name, weight) pairs")
    normalized: list[tuple[str, float]] = []
    seen: set[str] = set()
    for raw_entry in providers:
        try:
            raw_name, raw_weight = raw_entry
        except (TypeError, ValueError) as exc:
            raise TypeError("providers must contain (name, weight) pairs") from exc
        name = _validate_provider_name("providers", raw_name)
        if name in seen:
            raise ValueError("providers must not contain duplicate providers")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise TypeError("provider weights must be numbers")
        weight = float(raw_weight)
        if not math.isfinite(weight):
            raise ValueError("provider weights must be finite")
        seen.add(name)
        normalized.append((name, weight))
    return tuple(normalized)


def _normalize_current_provider(current: str | None) -> str | None:
    if current is None:
        return None
    return _validate_provider_name("current", current)


def _select_denied_names(
    *,
    attempt: int,
    current: str | None,
    exclude: Sequence[str],
) -> set[str] | None:
    try:
        _validate_attempt(attempt)
        denied = set(_normalize_provider_names("exclude", exclude))
        normalized_current = _normalize_current_provider(current)
    except (TypeError, ValueError):
        return None
    if normalized_current is not None:
        denied.add(normalized_current)
    return denied


@dataclass(frozen=True)
class StaticProviderRouter:
    """First-acceptable router. Wraps :func:`next_provider` for parity.

    ``available_providers`` is the ordered fallback list; the router
    returns the first entry that is neither ``current`` nor in
    ``exclude``. Stateless; safe to share across sessions.
    """

    available_providers: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "available_providers",
            _normalize_provider_names("available_providers", self.available_providers),
        )

    def select(
        self,
        *,
        scenario: FailureScenario,
        attempt: int,
        current: str | None,
        exclude: Sequence[str] = (),
    ) -> str | None:
        del scenario  # static router doesn't use it
        denied = _select_denied_names(
            attempt=attempt,
            current=current,
            exclude=exclude,
        )
        if denied is None:
            return None
        for name in self.available_providers:
            if name not in denied:
                return name
        return None


@dataclass
class RoundRobinProviderRouter:
    """Strict-rotation router. Stateful — keeps a cursor across calls."""

    available_providers: tuple[str, ...]
    _cursor: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.available_providers = _normalize_provider_names(
            "available_providers", self.available_providers
        )

    def select(
        self,
        *,
        scenario: FailureScenario,
        attempt: int,
        current: str | None,
        exclude: Sequence[str] = (),
    ) -> str | None:
        del scenario
        if not self.available_providers:
            return None
        denied = _select_denied_names(
            attempt=attempt,
            current=current,
            exclude=exclude,
        )
        if denied is None:
            return None
        n = len(self.available_providers)
        with self._lock:
            for offset in range(n):
                idx = (self._cursor + offset) % n
                name = self.available_providers[idx]
                if name not in denied:
                    self._cursor = (idx + 1) % n
                    return name
        return None


@dataclass(frozen=True)
class WeightedProviderRouter:
    """Pick the highest-weight non-excluded provider.

    ``providers`` is an ordered tuple of ``(name, weight)`` pairs.
    Zero or negative weights are treated as disabled. Ties favor
    earlier entries to keep selection deterministic.
    """

    providers: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "providers",
            _normalize_weighted_providers(self.providers),
        )

    def select(
        self,
        *,
        scenario: FailureScenario,
        attempt: int,
        current: str | None,
        exclude: Sequence[str] = (),
    ) -> str | None:
        del scenario
        denied = _select_denied_names(
            attempt=attempt,
            current=current,
            exclude=exclude,
        )
        if denied is None:
            return None
        best: tuple[float, int, str] | None = None
        for idx, (name, weight) in enumerate(self.providers):
            if weight <= 0 or name in denied:
                continue
            # Negate the index so an earlier idx wins ties.
            candidate = (weight, -idx, name)
            if best is None or candidate > best:
                best = candidate
        return best[2] if best is not None else None
