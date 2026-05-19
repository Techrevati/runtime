# Quality Gate

Graduated pass/fail evaluation with four ordered levels. Caller defines the mapping from observed metrics (confidence scores, test counts, signal strength) to a `QualityLevel`; the gate stays opinion-free.

## Quality levels

| Level | Numeric | Use |
|---|---|---|
| `MINIMAL` | 1 | Basic correctness — runs without crashing |
| `STANDARD` | 2 | Acceptable for an internal handoff |
| `STRICT` | 3 | Reviewable; ready for further work |
| `RELEASE` | 4 | Production-grade |

These names are advisory; what matters is the ordering. `QualityLevel` is an `IntEnum` so `>=` works.

## Usage

```python
from techrevati.runtime import QualityGate, QualityLevel

gate = QualityGate(required_level=QualityLevel.STRICT)
observed = my_quality_estimator(...)   # caller-defined mapping → QualityLevel

outcome = gate.evaluate(observed)
if outcome.satisfied:
    advance()
else:
    request_rework(outcome)
```

## API

```python
class QualityLevel(IntEnum): MINIMAL=1, STANDARD=2, STRICT=3, RELEASE=4

@dataclass(frozen=True)
class QualityGate:
    required_level: QualityLevel
    def evaluate(self, observed: QualityLevel | None) -> QualityGateOutcome: ...
    def is_satisfied_by(self, observed: QualityLevel) -> bool: ...

@dataclass(frozen=True)
class QualityGateOutcome:
    satisfied: bool
    required_level: QualityLevel
    observed_level: QualityLevel | None
```

`None` as `observed` always fails — no signal means no gate satisfaction.
