"""
Usage Tracking — Per-model cost estimation and budget tracking.

Pricing data is loaded from data/pricing.json. Callers may override or
extend it at runtime via register_pricing() / load_pricing_from_file().
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token pricing for a model."""
    input_per_million: float
    output_per_million: float
    cache_write_per_million: float = 0.0
    cache_read_per_million: float = 0.0


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
        )
        for name, spec in data["models"].items()
    }


# Mutable global registry. Use register_pricing() to update.
PRICING_TABLE: dict[str, ModelPricing] = _load_default_pricing()
_pricing_lock = threading.Lock()


def register_pricing(model: str, pricing: ModelPricing) -> None:
    """Register or override pricing for a model. Thread-safe.

    Matches are case-insensitive; the key is normalized to lower-case.
    """
    with _pricing_lock:
        PRICING_TABLE[model.lower()] = pricing


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
            )


def _resolve_pricing(model: str) -> ModelPricing:
    """Resolve pricing for a model. Falls back to zero (local/unknown).

    Tries exact match first, then longest prefix match so dated variants
    like 'model-a-20260514' map to 'model-a'.
    """
    model_lower = model.lower()
    if model_lower in PRICING_TABLE:
        return PRICING_TABLE[model_lower]
    best: tuple[int, ModelPricing] | None = None
    for key, pricing in PRICING_TABLE.items():
        if model_lower.startswith(key) and (best is None or len(key) > best[0]):
            best = (len(key), pricing)
    return best[1] if best else ModelPricing(0.0, 0.0)


@dataclass(frozen=True)
class UsageSnapshot:
    """Token usage for a single turn."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_read_tokens": self.cache_read_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UsageSnapshot:
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_write_tokens=data.get("cache_write_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
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
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record_turn(self, model: str, usage: UsageSnapshot) -> None:
        with self._lock:
            self.turns.append((model, usage))

    @property
    def total_input_tokens(self) -> int:
        return sum(u.input_tokens for _, u in self.turns)

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for _, u in self.turns)

    def cost_for_turn(self, model: str, usage: UsageSnapshot) -> float:
        p = _resolve_pricing(model)
        return (
            usage.input_tokens * p.input_per_million / 1_000_000
            + usage.output_tokens * p.output_per_million / 1_000_000
            + usage.cache_write_tokens * p.cache_write_per_million / 1_000_000
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
