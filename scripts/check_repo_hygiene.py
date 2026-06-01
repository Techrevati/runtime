"""Ensure generated local artifacts do not become repository files."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REQUIRED_IGNORE_PATTERNS = (
    "__pycache__/",
    "*.py[cod]",
    "build/",
    "dist/",
    ".coverage",
    ".coverage.*",
    ".hypothesis/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv",
    "/site",
    "htmlcov/",
)

GENERATED_ROOTS = {
    "build",
    "dist",
    "site",
    "htmlcov",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
}
GENERATED_SUFFIXES = {".pyc", ".pyo"}


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return [line for line in result.stdout.splitlines() if line]


def _check_gitignore(text: str) -> list[str]:
    patterns = {line.strip() for line in text.splitlines() if line.strip()}
    return [
        f".gitignore is missing required pattern: {pattern}"
        for pattern in REQUIRED_IGNORE_PATTERNS
        if pattern not in patterns
    ]


def _is_generated_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if not parts:
        return False
    if parts[0] in GENERATED_ROOTS:
        return True
    if "__pycache__" in parts:
        return True
    if normalized == ".coverage" or normalized.startswith(".coverage."):
        return True
    return Path(normalized).suffix in GENERATED_SUFFIXES


def _check_tracked_files(paths: list[str]) -> list[str]:
    return [
        f"generated artifact is tracked: {path}"
        for path in sorted(paths)
        if _is_generated_path(path)
    ]


def _failures(root: Path) -> list[str]:
    gitignore = root / ".gitignore"
    failures: list[str] = []
    if not gitignore.is_file():
        failures.append(".gitignore is missing")
    else:
        failures.extend(_check_gitignore(gitignore.read_text(encoding="utf-8")))
    failures.extend(_check_tracked_files(_tracked_files(root)))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root to scan.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("repository hygiene check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Repository hygiene check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
