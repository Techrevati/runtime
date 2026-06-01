"""Require workflow actions to be pinned to immutable commits."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

USES_RE = re.compile(r"^\s*uses:\s*(?P<value>[^\s#]+)")
SHA_REF_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$", re.IGNORECASE)


def _workflow_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for pattern in ("*.yml", "*.yaml"):
        yield from sorted(root.rglob(pattern))


def _is_pinned(value: str) -> bool:
    if value.startswith("./"):
        return True
    return bool(SHA_REF_RE.fullmatch(value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(".github/workflows"),
        help="Workflow file or directory to scan.",
    )
    args = parser.parse_args()

    failures: list[tuple[Path, int, str]] = []
    for path in _workflow_files(args.path):
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            match = USES_RE.match(line)
            if match and not _is_pinned(match.group("value")):
                failures.append((path, line_number, match.group("value")))

    if failures:
        print("workflow action pinning check failed:", file=sys.stderr)
        for path, line_number, value in failures:
            print(f"  {path}:{line_number}: {value}", file=sys.stderr)
        return 1

    print("Workflow action pinning check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
