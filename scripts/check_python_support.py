"""Ensure declared Python support matches CI coverage."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


SUPPORTED_PYTHONS = ("3.11", "3.12", "3.13")
BASELINE_PYTHON = SUPPORTED_PYTHONS[0]
RUFF_BASELINE = "py311"
CLASSIFIER_PREFIX = "Programming Language :: Python :: "


def _load_pyproject(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _literal_python_versions(text: str) -> list[tuple[str, ...]]:
    versions: list[tuple[str, ...]] = []
    pattern = re.compile(r"python-version:\s*(?P<value>\[[^\n]+\]|['\"][^'\"]+['\"])")
    for match in pattern.finditer(text):
        value = ast.literal_eval(match.group("value"))
        if isinstance(value, str):
            versions.append((value,))
        else:
            versions.append(tuple(str(item) for item in value))
    return versions


def _check_pyproject(pyproject: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    project = pyproject["project"]

    expected_requires = f">={BASELINE_PYTHON}"
    if project.get("requires-python") != expected_requires:
        failures.append(
            "pyproject requires-python "
            f"{project.get('requires-python')!r} != {expected_requires!r}"
        )

    classifiers = project.get("classifiers", [])
    python_classifiers = {
        classifier.removeprefix(CLASSIFIER_PREFIX)
        for classifier in classifiers
        if classifier.startswith(CLASSIFIER_PREFIX)
        and classifier.removeprefix(CLASSIFIER_PREFIX)[:1].isdigit()
    }
    expected_classifiers = set(SUPPORTED_PYTHONS)
    if python_classifiers != expected_classifiers:
        failures.append(
            "pyproject Python classifiers "
            f"{sorted(python_classifiers)!r} != {sorted(expected_classifiers)!r}"
        )

    mypy_python = pyproject.get("tool", {}).get("mypy", {}).get("python_version")
    if mypy_python != BASELINE_PYTHON:
        failures.append(f"mypy python_version {mypy_python!r} != {BASELINE_PYTHON!r}")

    ruff_target = pyproject.get("tool", {}).get("ruff", {}).get("target-version")
    if ruff_target != RUFF_BASELINE:
        failures.append(f"ruff target-version {ruff_target!r} != {RUFF_BASELINE!r}")

    return failures


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    expected_matrix = SUPPORTED_PYTHONS

    for filename in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / filename
        text = path.read_text(encoding="utf-8")
        matrices = _literal_python_versions(text)
        if not any(matrix == expected_matrix for matrix in matrices):
            failures.append(
                f"{path}: missing matrix python-version {list(expected_matrix)!r}"
            )

    for filename in ("docs.yml", "release.yml"):
        path = root / ".github" / "workflows" / filename
        text = path.read_text(encoding="utf-8")
        singles = [
            versions
            for versions in _literal_python_versions(text)
            if len(versions) == 1
        ]
        if (BASELINE_PYTHON,) not in singles:
            failures.append(
                f"{path}: missing single python-version {BASELINE_PYTHON!r}"
            )

    return failures


def _failures(root: Path) -> list[str]:
    return _check_pyproject(_load_pyproject(root)) + _check_workflows(root)


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
        print("python support check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"Python support check OK: {', '.join(SUPPORTED_PYTHONS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
