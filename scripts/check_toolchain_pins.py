"""Ensure CI/build toolchains are intentionally pinned."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


EXACT_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^<>=!~\s;]+$"
)
PINNED_EXTRAS = ("dev", "build", "docs", "release", "audit")
REQUIRED_EXTRAS = {
    "build": {"build", "twine"},
    "release": {"build", "cyclonedx-bom", "twine"},
    "audit": {"pip-audit"},
}


def _term(*parts: str) -> str:
    return "".join(parts)


FORBIDDEN_DOC_REQUIREMENTS = {_term("mkdocs", "-", "material")}


def _load_pyproject(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _requirement_name(requirement: str) -> str:
    name = requirement.split("==", 1)[0].split("[", 1)[0]
    return name.lower().replace("_", "-")


def _failures(root: Path) -> list[str]:
    pyproject = _load_pyproject(root)
    failures: list[str] = []

    for requirement in pyproject["build-system"].get("requires", []):
        if not EXACT_REQUIREMENT_RE.fullmatch(requirement):
            failures.append(f"build-system requirement is not exact: {requirement}")

    extras = pyproject["project"].get("optional-dependencies", {})
    for extra in PINNED_EXTRAS:
        requirements = extras.get(extra)
        if not requirements:
            failures.append(f"missing optional dependency group: {extra}")
            continue

        names = {_requirement_name(req) for req in requirements}
        missing = REQUIRED_EXTRAS.get(extra, set()) - names
        for name in sorted(missing):
            failures.append(f"{extra} extra is missing required tool: {name}")

        if extra == "docs":
            forbidden = FORBIDDEN_DOC_REQUIREMENTS & names
            for name in sorted(forbidden):
                failures.append(
                    f"docs extra includes forbidden theme dependency: {name}"
                )

        for requirement in requirements:
            if not EXACT_REQUIREMENT_RE.fullmatch(requirement):
                failures.append(
                    f"{extra} extra requirement is not exact: {requirement}"
                )

    return failures


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
        print("toolchain pin check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Toolchain pin check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
