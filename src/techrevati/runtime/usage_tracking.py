"""
Usage Tracking — Per-model cost estimation and budget tracking.

Pricing data is loaded from data/pricing.json. Callers may override or
extend it at runtime via register_pricing() / load_pricing_from_file().
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("techrevati.runtime.usage_tracking")
logger.addHandler(logging.NullHandler())


class UsageBoundExceededError(Exception):
    """Base class for cost/budget/limit overrun errors.

    Catching this catches both ``BudgetExceededError`` (cost overrun)
    and ``UsageLimitExceededError`` (token / tool-call overrun) — a
    softer migration target than catching one of the two directly.
    """


class BudgetExceededError(UsageBoundExceededError):
    """Raised when cumulative usage cost exceeds a configured budget.

    Carries the offending budget and current cost so callers can decide
    how to recover (escalate to human, switch to cheaper model, abort).
    """

    def __init__(self, budget_usd: float, current_cost_usd: float) -> None:
        self.budget_usd = budget_usd
        self.current_cost_usd = current_cost_usd
        super().__init__(
            f"budget exceeded: ${current_cost_usd:.4f} > ${budget_usd:.4f}"
        )


class UsageLimitExceededError(UsageBoundExceededError):
    """Raised when a non-cost usage dimension exceeds its configured cap.

    Distinct from ``BudgetExceededError`` because the failure mode is
    different (we hit a per-session token / tool-call cap, not a $$
    cap), and recovery options diverge: token caps usually mean
    "abort this loop", not "switch to a cheaper model".
    """

    def __init__(self, limit_name: str, observed: int, ceiling: int) -> None:
        self.limit_name = limit_name
        self.observed = observed
        self.ceiling = ceiling
        super().__init__(
            f"usage limit exceeded on '{limit_name}': "
            f"observed {observed} > ceiling {ceiling}"
        )


@dataclass(frozen=True)
class UsageLimits:
    """Per-session caps on each usage dimension.

    Set any subset of the fields; ``None`` means no limit for that
    dimension. ``UsageTracker.check_limits`` evaluates the limits
    against cumulative usage after each turn and raises
    ``UsageLimitExceededError`` on the first overrun.

    Mirrors Pydantic AI's ``UsageLimits`` shape so callers porting
    between the two get a familiar surface; the names match exactly
    on purpose.
    """

    request_tokens_max: int | None = None
    response_tokens_max: int | None = None
    total_tokens_max: int | None = None
    tool_calls_max: int | None = None
    cost_usd_max: float | None = None


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token pricing for a model.

    Cache pricing tiers reflect the 2026 ephemeral-cache shape used
    by major providers: 5-minute ephemeral cache writes are ~1.25x
    the input price, 1-hour writes are ~2x, reads are ~0.1x. Default
    ``cache_write_per_million`` is the historical / single-tier value
    and is used when the caller's ``UsageSnapshot.cache_ttl`` is
    ``None``.
    """

    input_per_million: float
    output_per_million: float
    cache_write_per_million: float = 0.0
    cache_read_per_million: float = 0.0
    cache_write_5min_per_million: float = 0.0
    cache_write_1h_per_million: float = 0.0

    def write_rate_for_ttl(self, ttl: str | None) -> float:
        """Return the per-million write rate for the given TTL hint.

        ``ttl=None`` → fall back to ``cache_write_per_million`` (legacy
        single-tier). ``"5m"`` and ``"1h"`` resolve to the 2026
        ephemeral tiers; unknown values also fall back so a
        misconfigured ``UsageSnapshot.cache_ttl`` doesn't crash cost
        calculation.
        """
        if ttl == "5m" and self.cache_write_5min_per_million:
            return self.cache_write_5min_per_million
        if ttl == "1h" and self.cache_write_1h_per_million:
            return self.cache_write_1h_per_million
        return self.cache_write_per_million


def _load_default_pricing() -> dict[str, ModelPricing]:
    """Load the bundled pricing.json into a model→ModelPricing dict."""
    raw = (
        resources.files("techrevati.runtime")
        .joinpath("data", "pricing.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(raw)
    return {
        name.lower(): ModelPricing(
            input_per_million=spec["input_per_million"],
            output_per_million=spec["output_per_million"],
            cache_write_per_million=spec.get("cache_write_per_million", 0.0),
            cache_read_per_million=spec.get("cache_read_per_million", 0.0),
            cache_write_5min_per_million=spec.get("cache_write_5min_per_million", 0.0),
            cache_write_1h_per_million=spec.get("cache_write_1h_per_million", 0.0),
        )
        for name, spec in data["models"].items()
    }


# Mutable global registry. Use register_pricing() to update.
PRICING_TABLE: dict[str, ModelPricing] = _load_default_pricing()
_pricing_lock = threading.Lock()

# Models we've already warned about (one warning per model per process).
_warned_unpriced_models: set[str] = set()


def has_pricing(model: str) -> bool:
    """Check whether pricing is registered for a model.

    Matches by exact name (case-insensitive) or longest-prefix, mirroring
    the resolution behavior of cost calculations. Returns False if the
    lookup falls back to the zero-cost default.
    """
    model_lower = model.lower()
    if model_lower in PRICING_TABLE:
        return True
    return any(model_lower.startswith(key) for key in PRICING_TABLE)


class PricingAlreadyRegisteredError(ValueError):
    """Raised on re-registration when ``on_conflict='error'``."""

    def __init__(self, model: str) -> None:
        super().__init__(
            f"Pricing for model {model!r} is already registered. "
            "Pass on_conflict='overwrite' to replace, or 'keep' to "
            "retain the existing entry."
        )
        self.model = model


def register_pricing(
    model: str,
    pricing: ModelPricing,
    *,
    on_conflict: Literal["overwrite", "error", "keep"] = "overwrite",
) -> None:
    """Register or override pricing for a model. Thread-safe.

    Matches are case-insensitive; the key is normalized to lower-case.

    ``on_conflict`` controls behavior when ``model`` is already in the
    pricing table:

    - ``"overwrite"`` (default, preserves 0.2.0 behavior) — replace the
      existing entry.
    - ``"error"`` — raise ``PricingAlreadyRegisteredError``. Use this in
      startup wiring where double-registration signals a configuration
      bug.
    - ``"keep"`` — leave the existing entry; the new pricing is dropped
      silently. Useful for "register defaults if not present" patterns.
    """
    model_lower = model.lower()
    with _pricing_lock:
        if model_lower in PRICING_TABLE:
            if on_conflict == "error":
                raise PricingAlreadyRegisteredError(model)
            if on_conflict == "keep":
                return
        PRICING_TABLE[model_lower] = pricing


def load_pricing_from_file(path: str | Path) -> None:
    """Load and merge pricing entries from a JSON file.

    Same schema as the bundled data/pricing.json. Existing entries
    are overwritten on conflict.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    with _pricing_lock:
        for name, spec in data["models"].items():
            PRICING_TABLE[name.lower()] = ModelPricing(
                input_per_million=spec["input_per_million"],
                output_per_million=spec["output_per_million"],
                cache_write_per_million=spec.get("cache_write_per_million", 0.0),
                cache_read_per_million=spec.get("cache_read_per_million", 0.0),
                cache_write_5min_per_million=spec.get(
                    "cache_write_5min_per_million", 0.0
                ),
                cache_write_1h_per_million=spec.get("cache_write_1h_per_million", 0.0),
            )


def _resolve_pricing(model: str) -> ModelPricing:
    """Resolve pricing for a model. Falls back to zero (local/unknown).

    Tries exact match first, then longest prefix match so dated variants
    like 'model-a-20260514' map to 'model-a'.

    Read path is guarded against concurrent ``register_pricing`` /
    ``load_pricing_from_file`` mutations: we snapshot the table under
    ``_pricing_lock`` and iterate the local copy, avoiding the
    ``RuntimeError: dictionary changed size during iteration`` window.
    """
    model_lower = model.lower()
    with _pricing_lock:
        if model_lower in PRICING_TABLE:
            return PRICING_TABLE[model_lower]
        snapshot = list(PRICING_TABLE.items())
    best: tuple[int, ModelPricing] | None = None
    for key, pricing in snapshot:
        if model_lower.startswith(key) and (best is None or len(key) > best[0]):
            best = (len(key), pricing)
    return best[1] if best else ModelPricing(0.0, 0.0)


@dataclass(frozen=True)
class UsageSnapshot:
    """Token usage for a single turn.

    ``cache_ttl`` is the optional ephemeral-cache hint
    (``"5m"`` / ``"1h"`` / ``None``) used to select between the
    cache-write pricing tiers. Leave it ``None`` for the
    legacy single-tier behavior.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cache_ttl: str | None = None
    tool_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "tool_calls": self.tool_calls,
        }
        if self.cache_ttl is not None:
            d["cache_ttl"] = self.cache_ttl
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageSnapshot:
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_write_tokens=data.get("cache_write_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            cache_ttl=data.get("cache_ttl"),
            tool_calls=data.get("tool_calls", 0),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> UsageSnapshot:
        return cls.from_dict(json.loads(s))


@dataclass
class UsageTracker:
    """Cumulative usage tracking with cost estimation."""

    turns: list[tuple[str, UsageSnapshot]] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def record_turn(self, model: str, usage: UsageSnapshot) -> None:
        with self._lock:
            self.turns.append((model, usage))
        if model and not has_pricing(model):
            with _pricing_lock:
                already_warned = model.lower() in _warned_unpriced_models
                if not already_warned:
                    _warned_unpriced_models.add(model.lower())
            if not already_warned:
                logger.warning(
                    "no pricing registered for model=%s; cost will be $0. "
                    "Call register_pricing() or load_pricing_from_file().",
                    model,
                )

    @property
    def total_input_tokens(self) -> int:
        return sum(u.input_tokens for _, u in self.turns)

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for _, u in self.turns)

    def cost_for_turn(self, model: str, usage: UsageSnapshot) -> float:
        p = _resolve_pricing(model)
        write_rate = p.write_rate_for_ttl(usage.cache_ttl)
        return (
            usage.input_tokens * p.input_per_million / 1_000_000
            + usage.output_tokens * p.output_per_million / 1_000_000
            + usage.cache_write_tokens * write_rate / 1_000_000
            + usage.cache_read_tokens * p.cache_read_per_million / 1_000_000
        )

    def total_cost(self) -> float:
        return sum(self.cost_for_turn(model, usage) for model, usage in self.turns)

    def format_cost(self) -> str:
        cost = self.total_cost()
        if cost < 0.01:
            return f"${cost:.4f}"
        return f"${cost:.2f}"

    def budget_remaining(self, budget_usd: float) -> float:
        return budget_usd - self.total_cost()

    def is_over_budget(self, budget_usd: float) -> bool:
        return self.total_cost() > budget_usd

    def summary(self) -> dict[str, Any]:
        return {
            "turns": len(self.turns),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost(), 4),
            "formatted_cost": self.format_cost(),
        }

    def per_model_summary(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for model, usage in self.turns:
            result[model] = result.get(model, 0.0) + self.cost_for_turn(model, usage)
        return result

    # -- Per-session usage caps -------------------------------------------

    @property
    def total_tool_calls(self) -> int:
        return sum(u.tool_calls for _, u in self.turns)

    def check_limits(self, limits: UsageLimits) -> None:
        """Raise ``UsageLimitExceededError`` on the first cap overrun.

        Order matches the dataclass declaration so a deterministic
        failure is reported even when multiple dimensions are over at
        once. ``cost_usd_max`` is handled here too so callers using
        ``UsageLimits`` can rely on a single check call; if both
        ``budget_usd`` (on the session) and ``cost_usd_max`` are
        configured, ``UsageLimits`` wins because it's the newer API.
        """
        if (
            limits.request_tokens_max is not None
            and self.total_input_tokens > limits.request_tokens_max
        ):
            raise UsageLimitExceededError(
                "request_tokens",
                self.total_input_tokens,
                limits.request_tokens_max,
            )
        if (
            limits.response_tokens_max is not None
            and self.total_output_tokens > limits.response_tokens_max
        ):
            raise UsageLimitExceededError(
                "response_tokens",
                self.total_output_tokens,
                limits.response_tokens_max,
            )
        if limits.total_tokens_max is not None:
            total = self.total_input_tokens + self.total_output_tokens
            if total > limits.total_tokens_max:
                raise UsageLimitExceededError(
                    "total_tokens", total, limits.total_tokens_max
                )
        if (
            limits.tool_calls_max is not None
            and self.total_tool_calls > limits.tool_calls_max
        ):
            raise UsageLimitExceededError(
                "tool_calls", self.total_tool_calls, limits.tool_calls_max
            )
        if limits.cost_usd_max is not None:
            cost = self.total_cost()
            if cost > limits.cost_usd_max:
                raise UsageLimitExceededError(
                    "cost_usd",
                    int(cost * 1_000_000),
                    int(limits.cost_usd_max * 1_000_000),
                )
