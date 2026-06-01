"""Install a pinned toolchain optional-dependency group."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


ALLOWED_GROUPS = {"audit", "build", "docs", "release"}


def _load_requirements(root: Path, group: str) -> list[str]:
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject: dict[str, Any] = tomllib.load(handle)
    requirements = pyproject["project"]["optional-dependencies"].get(group)
    if not requirements:
        raise SystemExit(f"unknown or empty toolchain group: {group}")
    return list(requirements)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=sorted(ALLOWED_GROUPS))
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pyproject.toml.",
    )
    args = parser.parse_args()

    requirements = _load_requirements(args.root, args.group)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", *requirements],
        timeout=600,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
