"""Ensure package metadata keeps the intended public contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


EXPECTED_PROJECT = {
    "name": "techrevati-runtime",
    "description": "Async-aware runtime primitives for multi-step LLM agent loops.",
    "readme": "README.md",
    "license": "MIT",
    "authors": [{"name": "Techrevati doo"}],
}
EXPECTED_KEYWORDS = {
    "agents",
    "orchestration",
    "retry",
    "circuit-breaker",
    "cost-tracking",
    "runtime",
}
REQUIRED_CLASSIFIERS = {
    "Development Status :: 4 - Beta",
    "Framework :: AsyncIO",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Topic :: Software Development :: Libraries",
    "Typing :: Typed",
}
EXPECTED_OTEL_EXTRA = {
    "opentelemetry-api>=1.27,<2",
    "opentelemetry-sdk>=1.27,<2",
    "opentelemetry-semantic-conventions>=0.48b0,<1",
}


def _load_pyproject(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _check_project(project: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    for key, expected in EXPECTED_PROJECT.items():
        if project.get(key) != expected:
            failures.append(f"project {key} {project.get(key)!r} != {expected!r}")

    if project.get("dependencies") != []:
        failures.append("project dependencies must stay empty")

    if "urls" in project:
        failures.append("project urls must not be published")

    keywords = set(project.get("keywords", []))
    if keywords != EXPECTED_KEYWORDS:
        failures.append(
            f"project keywords {sorted(keywords)!r} != {sorted(EXPECTED_KEYWORDS)!r}"
        )

    classifiers = set(project.get("classifiers", []))
    missing_classifiers = REQUIRED_CLASSIFIERS - classifiers
    for classifier in sorted(missing_classifiers):
        failures.append(f"project is missing classifier: {classifier}")

    extras = project.get("optional-dependencies", {})
    otel_extra = set(extras.get("otel", []))
    if otel_extra != EXPECTED_OTEL_EXTRA:
        failures.append(
            f"otel extra {sorted(otel_extra)!r} != {sorted(EXPECTED_OTEL_EXTRA)!r}"
        )

    return failures


def _check_source_files(root: Path) -> list[str]:
    failures: list[str] = []
    typed_marker = root / "src" / "techrevati" / "runtime" / "py.typed"
    pricing_file = root / "src" / "techrevati" / "runtime" / "data" / "pricing.json"

    if not typed_marker.is_file():
        failures.append("typed marker is missing")

    if not pricing_file.is_file():
        failures.append("pricing data file is missing")
    else:
        try:
            json.loads(pricing_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"pricing data is not valid json: {exc.msg}")

    return failures


def _failures(root: Path) -> list[str]:
    pyproject = _load_pyproject(root)
    return _check_project(dict(pyproject["project"])) + _check_source_files(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pyproject.toml.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("package policy check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Package policy check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
