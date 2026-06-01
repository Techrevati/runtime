"""Validate that a release tag matches a releasable project version."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


RELEASABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:rc\d+)|(?:\.post\d+))?$")


def _load_project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject: dict[str, Any] = tomllib.load(handle)
    return str(pyproject["project"]["version"])


def _check_release(version: str, tag: str | None) -> str | None:
    if not RELEASABLE_VERSION_RE.fullmatch(version):
        return (
            f"project version {version!r} is not a releasable version; "
            "use X.Y.Z, X.Y.ZrcN, or X.Y.Z.postN"
        )

    expected_tag = f"v{version}"
    if not tag:
        return f"release tag is missing; expected {expected_tag!r}"

    if tag != expected_tag:
        return f"release tag {tag!r} does not match project version {expected_tag!r}"

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pyproject.toml.",
    )
    parser.add_argument(
        "--version",
        help="Override project version; primarily useful for tests.",
    )
    parser.add_argument(
        "--tag",
        default=os.environ.get("GITHUB_REF_NAME"),
        help="Release tag to validate. Defaults to GITHUB_REF_NAME.",
    )
    args = parser.parse_args()

    version = args.version or _load_project_version(args.root)
    if error := _check_release(version, args.tag):
        print(f"release tag check failed: {error}", file=sys.stderr)
        return 1

    print(f"Release tag OK: v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
