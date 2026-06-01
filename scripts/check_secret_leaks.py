"""Reject committed secrets and credentials in repository text files."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

SKIPPED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "site",
}
BINARY_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".png",
    ".pyc",
    ".pyo",
    ".ttf",
    ".webp",
    ".whl",
    ".woff",
    ".woff2",
    ".zip",
}
MAX_TEXT_BYTES = 1_000_000
SECRET_NAME_PATTERN = (
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|"
    r"client[_-]?secret|private[_-]?key|secret|password|passwd|token)"
)
# Treat an alphanumeric run as the boundary so that a secret keyword embedded
# in a longer identifier (e.g. ``DATABASE_PASSWORD``, where ``_`` is a regex
# word char and ``\b`` would not fire before ``PASSWORD``) is still detected.
# Underscores, spaces, and start/end of line all count as boundaries.
_NAME_PREFIX = r"(?<![A-Za-z0-9])"
_NAME_SUFFIX = r"(?![A-Za-z0-9])"
SECRET_ASSIGNMENT_RE = re.compile(
    rf"{_NAME_PREFIX}{SECRET_NAME_PATTERN}{_NAME_SUFFIX}\s*(?::|=)\s*"
    r"(?P<quote>['\"])(?P<value>[^'\"\n]{12,})(?P=quote)",
    re.IGNORECASE,
)
UNQUOTED_ENV_SECRET_RE = re.compile(
    rf"{_NAME_PREFIX}{SECRET_NAME_PATTERN}\s*=\s*"
    r"(?P<value>[^'\"\s#][^\s#]{19,})",
    re.IGNORECASE,
)
CLOUD_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
PRIVATE_KEY_BLOCK_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
# Placeholder keywords must appear as a distinct token — bounded by start/end
# or a non-alphanumeric separator — so a real credential that merely *contains*
# a word like ``example`` as a substring (``examplekJHGsecretLIVE``) is NOT
# allow-listed. The structural forms (``${VAR}``, ``<token>``, ``__token__``)
# stay fully anchored.
_PLACEHOLDER_TOKENS = (
    r"redacted|placeholder|example|sample|dummy|changeme|change-me|"
    r"not-a-secret|test-only|your"
)
ALLOWLIST_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"^\$\{[^}]+\}$|"
    r"^<[^>]+>$|"
    r"^_+token_+$|"
    rf"(?:^|[^A-Za-z0-9])(?:{_PLACEHOLDER_TOKENS})(?![A-Za-z0-9])"
    r")"
)


def _candidate_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            if _is_scannable(path):
                yield path
            continue
        if not path.is_dir():
            continue
        for child in sorted(path.rglob("*")):
            if not child.is_file():
                continue
            if set(child.parts) & SKIPPED_DIRS:
                continue
            if _is_scannable(child):
                yield child


def _is_scannable(path: Path) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return False
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return False
    except OSError:
        return False
    return True


def _read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _allowed_secret_value(value: str) -> bool:
    normalized = value.strip()
    if ALLOWLIST_VALUE_RE.search(normalized):
        return True
    if len(set(normalized)) <= 2:
        return True
    return False


def _check_text(path: Path, text: str) -> list[str]:
    failures: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if PRIVATE_KEY_BLOCK_RE.search(line):
            failures.append(f"{path}:{line_number}: private key block is committed")
        if CLOUD_ACCESS_KEY_RE.search(line):
            failures.append(f"{path}:{line_number}: cloud access key is committed")

        for match in SECRET_ASSIGNMENT_RE.finditer(line):
            value = match.group("value")
            if not _allowed_secret_value(value):
                failures.append(
                    f"{path}:{line_number}: possible hard-coded secret assignment"
                )

        if match := UNQUOTED_ENV_SECRET_RE.search(line):
            value = match.group("value")
            if not _allowed_secret_value(value):
                failures.append(
                    f"{path}:{line_number}: possible hard-coded secret environment "
                    "value"
                )
    return failures


def _failures(paths: Iterable[Path]) -> list[str]:
    failures: list[str] = []
    for path in _candidate_files(paths):
        text = _read_text(path)
        if text is None:
            continue
        failures.extend(_check_text(path, text))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(".")],
        help="Repository files or directories to scan.",
    )
    args = parser.parse_args()

    failures = _failures(args.paths)
    if failures:
        print("secret leak check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Secret leak check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
