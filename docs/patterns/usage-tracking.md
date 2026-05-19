# Usage Tracking

Per-model token aggregation with caller-provided pricing. No bundled rates — register what you use.

## Usage

```python
from techrevati.runtime import (
    UsageTracker, UsageSnapshot, ModelPricing,
    register_pricing, load_pricing_from_file,
)

# Register pricing in code...
register_pricing("model-a", ModelPricing(input_per_million=3.0, output_per_million=15.0))

# ...or load a JSON file you control.
load_pricing_from_file("/etc/myorg/pricing.json")

tracker = UsageTracker()
tracker.record_turn("model-a", UsageSnapshot(input_tokens=5000, output_tokens=1200))

print(tracker.format_cost())            # "$0.03"
print(tracker.per_model_summary())      # {"model-a": 0.033}

if tracker.is_over_budget(budget_usd=10.0):
    raise RuntimeError("budget exceeded")
```

## Pricing file format

```json
{
  "models": {
    "model-a": {"input_per_million": 3.0, "output_per_million": 15.0},
    "model-b": {
      "input_per_million": 1.0,
      "output_per_million": 4.0,
      "cache_write_per_million": 1.25,
      "cache_read_per_million": 0.1
    }
  }
}
```

Both `register_pricing` and `load_pricing_from_file` are thread-safe. Subsequent registrations overwrite earlier ones for the same key.

## Model name resolution

`UsageTracker` resolves model names case-insensitively, with **longest-prefix match** as a fallback. That means a dated variant like `model-a-20260514` resolves to `model-a` if that's the most specific registered entry. Unknown models fall back to zero pricing (treated as free).

## API

```python
@dataclass(frozen=True)
class ModelPricing:
    input_per_million: float
    output_per_million: float
    cache_write_per_million: float = 0.0
    cache_read_per_million: float = 0.0

@dataclass(frozen=True)
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    # to_dict / from_dict / to_json / from_json round-trip

@dataclass
class UsageTracker:
    def record_turn(model: str, usage: UsageSnapshot) -> None
    @property total_input_tokens: int
    @property total_output_tokens: int
    def cost_for_turn(model, usage) -> float
    def total_cost() -> float
    def format_cost() -> str
    def budget_remaining(budget_usd) -> float
    def is_over_budget(budget_usd) -> bool
    def summary() -> dict
    def per_model_summary() -> dict[str, float]

# Module functions
register_pricing(model: str, pricing: ModelPricing) -> None
load_pricing_from_file(path: str | Path) -> None
```

## When not to use it

If your fault model is "providers bill us directly and we read from their dashboards", skip this. The tracker is for in-process budget enforcement and per-session cost telemetry.
