"""Ensure package version metadata is consistent."""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


def _load_project(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        return dict(tomllib.load(handle)["project"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pyproject.toml.",
    )
    args = parser.parse_args()

    project = _load_project(args.root)
    name = str(project["name"])
    expected_version = str(project["version"])

    try:
        installed_version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        print(
            f"version check failed: package {name!r} is not installed",
            file=sys.stderr,
        )
        return 1

    from techrevati import runtime

    failures: list[str] = []
    if installed_version != expected_version:
        failures.append(
            f"installed metadata version {installed_version!r} "
            f"!= pyproject version {expected_version!r}"
        )
    if runtime.__version__ != expected_version:
        failures.append(
            f"runtime.__version__ {runtime.__version__!r} "
            f"!= pyproject version {expected_version!r}"
        )

    if failures:
        print("version check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"Version consistency OK: {name} {expected_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
