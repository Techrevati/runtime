"""
Usage Tracking — Per-model cost estimation and budget tracking.

Pricing data is loaded from data/pricing.json. Callers may override or
extend it at runtime via register_pricing() / load_pricing_from_file().
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger("techrevati.runtime.usage_tracking")
logger.addHandler(logging.NullHandler())

_CONFLICT_ACTIONS = frozenset({"overwrite", "error", "keep"})


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
        budget_usd = _validate_finite_amount("budget_usd", budget_usd, allow_zero=True)
        current_cost_usd = _validate_finite_amount(
            "current_cost_usd", current_cost_usd, allow_zero=True
        )
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
        if not isinstance(limit_name, str) or not limit_name.strip():
            raise ValueError("limit_name must be a non-empty string")
        self.limit_name = limit_name.strip()
        self.observed = _validate_non_negative_int("observed", observed)
        self.ceiling = _validate_non_negative_int("ceiling", ceiling)
        super().__init__(
            f"usage limit exceeded on '{self.limit_name}': "
            f"observed {self.observed} > ceiling {self.ceiling}"
        )


def _validate_finite_amount(name: str, value: float, *, allow_zero: bool) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(amount):
        raise ValueError(f"{name} must be finite")
    if allow_zero:
        if amount < 0:
            raise ValueError(f"{name} must be >= 0")
    elif amount <= 0:
        raise ValueError(f"{name} must be > 0")
    return amount


def _validate_non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return value


def _validate_optional_non_negative_int(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    return _validate_non_negative_int(name, value)


def _validate_model_name(model: str) -> str:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    return model.strip()


def _normalize_optional_model_name(model: str) -> str:
    if not isinstance(model, str):
        raise ValueError("model must be a string")
    stripped = model.strip()
    if not stripped and model:
        raise ValueError("model must be a non-empty string or empty sentinel")
    return stripped


def _validate_conflict_action(
    value: object,
) -> Literal["overwrite", "error", "keep"]:
    if not isinstance(value, str) or value not in _CONFLICT_ACTIONS:
        raise ValueError("on_conflict must be one of: overwrite, error, keep")
    if value == "overwrite":
        return "overwrite"
    if value == "error":
        return "error"
    return "keep"


def _validate_pricing(pricing: ModelPricing) -> ModelPricing:
    if not isinstance(pricing, ModelPricing):
        raise TypeError("pricing must be a ModelPricing instance")
    return pricing


def _validate_usage_snapshot(usage: UsageSnapshot) -> UsageSnapshot:
    if not isinstance(usage, UsageSnapshot):
        raise TypeError("usage must be a UsageSnapshot instance")
    return usage


@dataclass(frozen=True)
class UsageLimits:
    """Per-session caps on each usage dimension.

    Set any subset of the fields; ``None`` means no limit for that
    dimension. ``UsageTracker.check_limits`` evaluates the limits
    against cumulative usage after each turn and raises
    ``UsageLimitExceededError`` on the first overrun.

    Field names are intentionally explicit so callers can map them to external
    usage schemas without extra adapters.
    """

    request_tokens_max: int | None = None
    response_tokens_max: int | None = None
    total_tokens_max: int | None = None
    tool_calls_max: int | None = None
    cost_usd_max: float | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "request_tokens_max",
            "response_tokens_max",
            "total_tokens_max",
            "tool_calls_max",
        ):
            object.__setattr__(
                self,
                field_name,
                _validate_optional_non_negative_int(
                    field_name, getattr(self, field_name)
                ),
            )
        if self.cost_usd_max is not None:
            object.__setattr__(
                self,
                "cost_usd_max",
                _validate_finite_amount(
                    "cost_usd_max", self.cost_usd_max, allow_zero=True
                ),
            )


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

    def __post_init__(self) -> None:
        for field_name in (
            "input_per_million",
            "output_per_million",
            "cache_write_per_million",
            "cache_read_per_million",
            "cache_write_5min_per_million",
            "cache_write_1h_per_million",
        ):
            object.__setattr__(
                self,
                field_name,
                _validate_finite_amount(
                    field_name, getattr(self, field_name), allow_zero=True
                ),
            )

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
    return _pricing_table_from_payload(json.loads(raw))


def _pricing_from_spec(model: str, spec: object) -> tuple[str, ModelPricing]:
    model_lower = _validate_model_name(model).lower()
    if not isinstance(spec, dict):
        raise TypeError(f"pricing spec for model {model!r} must be a JSON object")
    missing_fields = [
        field_name
        for field_name in ("input_per_million", "output_per_million")
        if field_name not in spec
    ]
    if missing_fields:
        missing = ", ".join(missing_fields)
        raise ValueError(f"pricing spec for model {model!r} missing: {missing}")
    return model_lower, ModelPricing(
        input_per_million=spec["input_per_million"],
        output_per_million=spec["output_per_million"],
        cache_write_per_million=spec.get("cache_write_per_million", 0.0),
        cache_read_per_million=spec.get("cache_read_per_million", 0.0),
        cache_write_5min_per_million=spec.get("cache_write_5min_per_million", 0.0),
        cache_write_1h_per_million=spec.get("cache_write_1h_per_million", 0.0),
    )


def _pricing_table_from_payload(data: object) -> dict[str, ModelPricing]:
    if not isinstance(data, dict):
        raise TypeError("pricing file must contain a JSON object")
    models = data.get("models")
    if not isinstance(models, dict):
        raise TypeError("pricing file 'models' must be a JSON object")
    loaded: dict[str, ModelPricing] = {}
    for name, spec in models.items():
        if not isinstance(name, str):
            raise TypeError("pricing model names must be strings")
        model_lower, pricing = _pricing_from_spec(name, spec)
        loaded[model_lower] = pricing
    return loaded


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
    if not isinstance(model, str) or not model.strip():
        return False
    model_lower = model.strip().lower()
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
    model_lower = _validate_model_name(model).lower()
    pricing = _validate_pricing(pricing)
    conflict_action = _validate_conflict_action(on_conflict)
    with _pricing_lock:
        if model_lower in PRICING_TABLE:
            if conflict_action == "error":
                raise PricingAlreadyRegisteredError(model)
            if conflict_action == "keep":
                return
        PRICING_TABLE[model_lower] = pricing


def load_pricing_from_file(path: str | Path) -> None:
    """Load and merge pricing entries from a JSON file.

    Same schema as the bundled data/pricing.json. Existing entries
    are overwritten on conflict.
    """
    loaded = _pricing_table_from_payload(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
    with _pricing_lock:
        PRICING_TABLE.update(loaded)


def _resolve_pricing(model: str) -> ModelPricing:
    """Resolve pricing for a model. Falls back to zero (local/unknown).

    Tries exact match first, then longest prefix match so dated variants
    like 'model-a-20260514' map to 'model-a'.

    Read path is guarded against concurrent ``register_pricing`` /
    ``load_pricing_from_file`` mutations: we snapshot the table under
    ``_pricing_lock`` and iterate the local copy, avoiding the
    ``RuntimeError: dictionary changed size during iteration`` window.
    """
    model_lower = _normalize_optional_model_name(model).lower()
    if not model_lower:
        return ModelPricing(0.0, 0.0)
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

    def __post_init__(self) -> None:
        for field_name in (
            "input_tokens",
            "output_tokens",
            "cache_write_tokens",
            "cache_read_tokens",
            "tool_calls",
        ):
            object.__setattr__(
                self,
                field_name,
                _validate_non_negative_int(field_name, getattr(self, field_name)),
            )
        if self.cache_ttl is not None:
            if not isinstance(self.cache_ttl, str):
                raise ValueError("cache_ttl must be a string or None")
            if not self.cache_ttl.strip():
                raise ValueError("cache_ttl must not be empty")
            object.__setattr__(self, "cache_ttl", self.cache_ttl.strip())

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
        if not isinstance(data, dict):
            raise TypeError("usage snapshot data must be a dictionary")
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


def _copy_usage_snapshot(usage: UsageSnapshot) -> UsageSnapshot:
    return UsageSnapshot.from_dict(usage.to_dict())


@dataclass
class UsageTracker:
    """Cumulative usage tracking with cost estimation."""

    turns: list[tuple[str, UsageSnapshot]] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.turns = [
            (
                _normalize_optional_model_name(model),
                _copy_usage_snapshot(_validate_usage_snapshot(usage)),
            )
            for model, usage in self.turns
        ]

    def _snapshot_turns(self) -> list[tuple[str, UsageSnapshot]]:
        with self._lock:
            return [(model, _copy_usage_snapshot(usage)) for model, usage in self.turns]

    def record_turn(self, model: str, usage: UsageSnapshot) -> None:
        model = _normalize_optional_model_name(model)
        usage = _validate_usage_snapshot(usage)
        with self._lock:
            self.turns.append((model, _copy_usage_snapshot(usage)))
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
        return sum(u.input_tokens for _, u in self._snapshot_turns())

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for _, u in self._snapshot_turns())

    def cost_for_turn(self, model: str, usage: UsageSnapshot) -> float:
        model = _normalize_optional_model_name(model)
        usage = _validate_usage_snapshot(usage)
        p = _resolve_pricing(model)
        write_rate = p.write_rate_for_ttl(usage.cache_ttl)
        return (
            usage.input_tokens * p.input_per_million / 1_000_000
            + usage.output_tokens * p.output_per_million / 1_000_000
            + usage.cache_write_tokens * write_rate / 1_000_000
            + usage.cache_read_tokens * p.cache_read_per_million / 1_000_000
        )

    def total_cost(self) -> float:
        return sum(
            self.cost_for_turn(model, usage) for model, usage in self._snapshot_turns()
        )

    @staticmethod
    def _format_cost_value(cost: float) -> str:
        cost = _validate_finite_amount("cost", cost, allow_zero=True)
        if cost < 0.01:
            return f"${cost:.4f}"
        return f"${cost:.2f}"

    def format_cost(self) -> str:
        return self._format_cost_value(self.total_cost())

    def budget_remaining(self, budget_usd: float) -> float:
        budget_usd = _validate_finite_amount("budget_usd", budget_usd, allow_zero=True)
        return budget_usd - self.total_cost()

    def is_over_budget(self, budget_usd: float) -> bool:
        budget_usd = _validate_finite_amount("budget_usd", budget_usd, allow_zero=True)
        return self.total_cost() > budget_usd

    def summary(self) -> dict[str, Any]:
        turns = self._snapshot_turns()
        total_input_tokens = sum(u.input_tokens for _, u in turns)
        total_output_tokens = sum(u.output_tokens for _, u in turns)
        total_cost_usd = sum(self.cost_for_turn(model, usage) for model, usage in turns)
        return {
            "turns": len(turns),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_usd": round(total_cost_usd, 4),
            "formatted_cost": self._format_cost_value(total_cost_usd),
        }

    def per_model_summary(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for model, usage in self._snapshot_turns():
            result[model] = result.get(model, 0.0) + self.cost_for_turn(model, usage)
        return result

    # -- Per-session usage caps -------------------------------------------

    @property
    def total_tool_calls(self) -> int:
        return sum(u.tool_calls for _, u in self._snapshot_turns())

    def check_limits(self, limits: UsageLimits) -> None:
        """Raise ``UsageLimitExceededError`` on the first cap overrun.

        Order matches the dataclass declaration so a deterministic
        failure is reported even when multiple dimensions are over at
        once. ``cost_usd_max`` is handled here too so callers using
        ``UsageLimits`` can rely on a single check call; if both
        ``budget_usd`` (on the session) and ``cost_usd_max`` are
        configured, ``UsageLimits`` wins because it's the newer API.
        """
        if not isinstance(limits, UsageLimits):
            raise TypeError("limits must be a UsageLimits instance")
        turns = self._snapshot_turns()
        total_input_tokens = sum(u.input_tokens for _, u in turns)
        total_output_tokens = sum(u.output_tokens for _, u in turns)
        total_tool_calls = sum(u.tool_calls for _, u in turns)
        if (
            limits.request_tokens_max is not None
            and total_input_tokens > limits.request_tokens_max
        ):
            raise UsageLimitExceededError(
                "request_tokens",
                total_input_tokens,
                limits.request_tokens_max,
            )
        if (
            limits.response_tokens_max is not None
            and total_output_tokens > limits.response_tokens_max
        ):
            raise UsageLimitExceededError(
                "response_tokens",
                total_output_tokens,
                limits.response_tokens_max,
            )
        if limits.total_tokens_max is not None:
            total = total_input_tokens + total_output_tokens
            if total > limits.total_tokens_max:
                raise UsageLimitExceededError(
                    "total_tokens", total, limits.total_tokens_max
                )
        if (
            limits.tool_calls_max is not None
            and total_tool_calls > limits.tool_calls_max
        ):
            raise UsageLimitExceededError(
                "tool_calls", total_tool_calls, limits.tool_calls_max
            )
        if limits.cost_usd_max is not None:
            cost = sum(self.cost_for_turn(model, usage) for model, usage in turns)
            if cost > limits.cost_usd_max:
                raise UsageLimitExceededError(
                    "cost_usd",
                    int(cost * 1_000_000),
                    int(limits.cost_usd_max * 1_000_000),
                )
