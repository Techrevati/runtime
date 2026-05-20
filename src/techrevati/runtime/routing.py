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

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from techrevati.runtime.retry_policy import FailureScenario, next_provider

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


@dataclass(frozen=True)
class StaticProviderRouter:
    """First-acceptable router. Wraps :func:`next_provider` for parity.

    ``available_providers`` is the ordered fallback list; the router
    returns the first entry that is neither ``current`` nor in
    ``exclude``. Stateless; safe to share across sessions.
    """

    available_providers: tuple[str, ...]

    def select(
        self,
        *,
        scenario: FailureScenario,
        attempt: int,
        current: str | None,
        exclude: Sequence[str] = (),
    ) -> str | None:
        del scenario, attempt  # static router doesn't use them
        denied = set(exclude)
        if current is not None:
            denied.add(current)
        for name in self.available_providers:
            if name not in denied:
                return name
        # Fall through: legacy callers expect ``next_provider`` semantics.
        return next_provider(list(self.available_providers), current or "")


@dataclass
class RoundRobinProviderRouter:
    """Strict-rotation router. Stateful — keeps a cursor across calls."""

    available_providers: tuple[str, ...]
    _cursor: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def select(
        self,
        *,
        scenario: FailureScenario,
        attempt: int,
        current: str | None,
        exclude: Sequence[str] = (),
    ) -> str | None:
        del scenario, attempt
        if not self.available_providers:
            return None
        denied = set(exclude)
        if current is not None:
            denied.add(current)
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

    def select(
        self,
        *,
        scenario: FailureScenario,
        attempt: int,
        current: str | None,
        exclude: Sequence[str] = (),
    ) -> str | None:
        del scenario, attempt
        denied = set(exclude)
        if current is not None:
            denied.add(current)
        best: tuple[float, int, str] | None = None
        for idx, (name, weight) in enumerate(self.providers):
            if weight <= 0 or name in denied:
                continue
            # Negate the index so an earlier idx wins ties.
            candidate = (weight, -idx, name)
            if best is None or candidate > best:
                best = candidate
        return best[2] if best is not None else None
