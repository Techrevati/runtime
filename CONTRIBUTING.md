# Contributing to techrevati-runtime

Thanks for considering a contribution. This document covers the
mechanical bits — branching, tests, type checking, release process.
For *what* to build, see the [issue tracker](https://github.com/Techrevati/runtime/issues)
or open a discussion before writing a lot of code.

## Setup

```bash
git clone https://github.com/Techrevati/runtime
cd runtime
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Local checks (run before pushing)

```bash
ruff check src tests
ruff format --check src tests
mypy src --strict
pytest --cov=src/techrevati --cov-branch --cov-fail-under=90
```

CI runs exactly these commands across Python 3.11, 3.12, and 3.13.
If they pass locally, they should pass in CI. Tool versions are
pinned in `[project.optional-dependencies] dev` to match
`.pre-commit-config.yaml`; if you upgrade one, upgrade both.

## Adding a new primitive

The runtime is composed of small, single-responsibility primitives
that work standalone *and* compose through `Orchestrator`. To add
one:

1. **Drop it into `src/techrevati/runtime/`** as a new module. Frozen
   dataclasses for value types; `threading.Lock` + `asyncio.Lock` for
   shared state if needed.
2. **Add `py.typed` discipline** — every public API has type
   annotations; mypy `--strict` must pass.
3. **Write the test file first** in `tests/test_<module>.py`.
   Deterministic timing: inject a `clock` parameter instead of using
   `time.sleep` or wall-clock checks.
4. **Re-export the public surface** from
   `src/techrevati/runtime/__init__.py` and add to `__all__`.
5. **Wire into `Orchestrator`** if it makes sense as a session-level
   concern. Otherwise document standalone usage.
6. **Write a pattern doc** at `docs/patterns/<name>.md` following the
   "Quick example / When to use / When not to use / Anti-patterns /
   Tuning / See also" template from
   [`docs/patterns/orchestrator.md`](docs/patterns/orchestrator.md).
7. **Add an API reference stub** at `docs/api/<name>.md` —
   `mkdocstrings` will generate the rest.
8. **Update `CHANGELOG.md`** under the unreleased section using the
   [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## Adding a new `PolicyCondition`

Subclass `PolicyCondition` in `policy_engine.py`, implement
`matches(ctx) -> bool`, then add a `__repr__` so log lines remain
readable. Update `PhaseContext` if the condition needs new state.
Tests go in `tests/test_policy_engine.py`.

## Testing

The example-based tests in `tests/test_<module>.py` are the day-to-day
unit tests. Two adjacent techniques are also in routine use; reach for
them when they fit, not as a matter of course.

### Property-based testing with Hypothesis

`tests/test_property_<module>.py` files use
[Hypothesis](https://hypothesis.readthedocs.io/) to assert invariants
over a wider input space than enumerated examples can cover. Two
templates already in the tree:

- `tests/test_property_retry_policy.py` — `@given` over strings to
  prove `classify_exception` never crashes on exotic input, and over
  attempt counts to prove `backoff_delay` stays in `[0, cap]`.
- `tests/test_property_circuit_breaker.py` —
  `RuleBasedStateMachine` driving CLOSED ↔ OPEN ↔ HALF_OPEN transitions
  with random success/failure sequences, asserting state-machine
  invariants after every step.

Reach for Hypothesis when:

- the input is text/numbers/structures the example tests can only
  cover by hand-picking representatives (classifiers, parsers, math);
- the unit is a state machine and you can name an invariant that must
  hold after every legal operation (the circuit breaker is the canonical
  example here); or
- you fixed a fuzz-style bug and want a regression strategy that won't
  shrink to "the exact value I caught it on".

Don't reach for it when the assertion you'd write is essentially the
implementation re-stated, or when the cost of generating valid inputs
exceeds the cost of one or two example tests.

### ManualClock injection pattern

Every primitive that depends on monotonic time accepts a `clock`
parameter — `CircuitBreaker(name, clock=...)` is the prototype. In
production code the default is `time.monotonic`; in tests, inject the
`ManualClock` from `tests/conftest.py`:

```python
from tests.conftest import ManualClock  # or use the `manual_clock` fixture

clock = ManualClock(start=1000.0)
breaker = CircuitBreaker("api", recovery_timeout_seconds=60.0, clock=clock)
# ... drive the breaker to OPEN ...
clock.advance(61.0)  # cross the recovery timeout deterministically
assert breaker.state() is CircuitState.HALF_OPEN
```

Why this matters: tests that call `time.sleep(60)` are slow, flaky, and
lie about what they're testing (they prove the OS scheduler honors the
sleep, not that the unit transitions on time). `ManualClock` lets the
test name the exact instant it cares about, and the suite stays fast.

When you add a new primitive that depends on time, accept a `clock`
parameter with the same shape (`Callable[[], float]`) so the existing
`ManualClock` plugs in unchanged.

## Async-vs-sync invariants

When you add a sync primitive, add the async sibling in the same
file (mainstream pattern — `httpx.Client` + `httpx.AsyncClient`).
Share state via an internal `_Core` dataclass when behavior must
match, or accept independent state when use cases differ
(`CircuitBreaker` / `AsyncCircuitBreaker` chose independent state).

## Commits and PRs

- **Conventional Commits** preferred: `feat:`, `fix:`, `docs:`,
  `chore:`, `release:`. The dev branch tags follow this; the release
  tags are plain `release:`.
- **One concern per PR**. Tests and code for the same feature can
  share a PR; refactors that touch unrelated code go separately.
- **Squash on merge** is the default. The dev-tag commits in `git log`
  show what a clean Sprint-sized PR looks like.
- **Sign-off welcome but not required.** Commits and PR bodies end at
  the last content line — no trailers, no auto-generated footers.

## Release process (for maintainers)

1. Confirm `mypy --strict`, `ruff`, `pytest` are green locally and
   in CI on `main`.
2. Bump `version` in `pyproject.toml` AND `__version__` in
   `src/techrevati/runtime/__init__.py`. They must match.
3. Add a CHANGELOG entry. Move "Unreleased" to the new version.
4. Open a release PR. CI must pass before merge.
5. Tag `v<version>` on `main`. The PyPI publish workflow runs on tag
   push via trusted publishing.
6. `mkdocs gh-deploy` runs from the docs workflow on the same tag.

## Code style

- Frozen dataclasses for value types.
- `__all__` on every public module.
- Docstrings: Google-style. Top of module = one-paragraph overview.
- No emojis in source unless the user asked.
- Comments explain *why*, not *what*. If a comment paraphrases the
  identifier, delete it.
