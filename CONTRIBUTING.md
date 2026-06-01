# Contributing

Author: Techrevati doo

This document covers the mechanical bits: setup, tests, type checking, release
process, and code style.

## Setup

```bash
git clone <repository-url>
cd runtime
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Local Checks

```bash
ruff check src tests
ruff format --check src tests
mypy src --strict
pytest --cov=src/techrevati --cov-branch --cov-fail-under=90
```

CI runs the same checks across supported Python versions. Tool versions are
pinned in `[project.optional-dependencies] dev` and should stay aligned with
`.pre-commit-config.yaml`.

## Adding a New Primitive

1. Add the module under `src/techrevati/runtime/`.
2. Keep public APIs typed; `mypy --strict` must pass.
3. Add focused tests in `tests/test_<module>.py`.
4. Re-export the public surface from `src/techrevati/runtime/__init__.py`.
5. Wire into `Orchestrator` only when it is a session-level concern.
6. Add a pattern doc under `docs/patterns/`.
7. Add an API reference stub under `docs/api/`.
8. Update `CHANGELOG.md`.

## Testing Guidance

Use deterministic clocks for time-sensitive behavior. Prefer injected clocks
over wall-clock sleeps so tests stay fast and stable.

Use property-based tests when the unit has a clear invariant over broad inputs,
such as classifiers, math, parsers, or state machines.

## Async and Sync Invariants

When adding a sync primitive that has async use cases, add the async sibling in
the same module and keep behavior aligned through shared helpers where practical.

## Release Process

1. Confirm lint, format, type checks, tests, coverage, and docs are green.
2. Bump `version` in `pyproject.toml`.
3. Update `CHANGELOG.md`.
4. Open a release change and wait for CI.
5. Tag `v<version>` from the release commit.
6. Publish artifacts through the configured release workflow.

## Code Style

- Frozen dataclasses for value types.
- `__all__` on public modules.
- Google-style docstrings.
- Comments explain why, not what.
- Keep unrelated refactors out of feature changes.
