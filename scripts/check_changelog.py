"""Ensure CHANGELOG.md documents the current package version."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


HEADING_RE = re.compile(
    r"^##\s+(?:\[(?P<bracket>[^\]]+)\]|(?P<plain>\S+))\s+-\s+"
    r"(?P<released>\d{4}-\d{2}-\d{2})\s*$"
)
PLACEHOLDER_BODIES = {"coming soon", "tbd", "todo"}


class ChangelogEntry(NamedTuple):
    version: str
    released: str
    line_number: int
    body: str


def _load_project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject: dict[str, Any] = tomllib.load(handle)
    return str(pyproject["project"]["version"])


def _entries(text: str) -> list[ChangelogEntry]:
    lines = text.splitlines()
    headings: list[tuple[int, re.Match[str]]] = []

    for index, line in enumerate(lines):
        if match := HEADING_RE.match(line):
            headings.append((index, match))

    entries: list[ChangelogEntry] = []
    for position, (line_index, match) in enumerate(headings):
        next_line_index = (
            headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        )
        body = "\n".join(lines[line_index + 1 : next_line_index]).strip()
        version = match.group("bracket") or match.group("plain")
        entries.append(
            ChangelogEntry(
                version=version,
                released=match.group("released"),
                line_number=line_index + 1,
                body=body,
            )
        )

    return entries


def _check_changelog(
    version: str, text: str, *, today: date | None = None
) -> list[str]:
    entries = _entries(text)
    matching = [entry for entry in entries if entry.version == version]
    if not matching:
        return [
            "CHANGELOG.md is missing an entry for "
            f"{version!r}; expected heading '## {version} - YYYY-MM-DD'"
        ]

    failures: list[str] = []
    if entries[0].version != version:
        failures.append(
            "CHANGELOG.md current version entry must be first; "
            f"found {entries[0].version!r} before {version!r}"
        )

    if len(matching) > 1:
        lines = ", ".join(str(entry.line_number) for entry in matching)
        failures.append(f"CHANGELOG.md has duplicate entries for {version!r}: {lines}")

    entry = matching[0]
    released_date: date | None = None
    try:
        released_date = date.fromisoformat(entry.released)
    except ValueError:
        failures.append(
            f"CHANGELOG.md entry for {version!r} has invalid date {entry.released!r}"
        )
    if released_date is not None and released_date > (today or date.today()):
        failures.append(
            f"CHANGELOG.md entry for {version!r} has future release date "
            f"{entry.released!r}"
        )

    compact_body = " ".join(entry.body.split()).lower()
    if not compact_body:
        failures.append(f"CHANGELOG.md entry for {version!r} has no body")
    elif compact_body in PLACEHOLDER_BODIES:
        failures.append(
            f"CHANGELOG.md entry for {version!r} still contains placeholder text"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pyproject.toml and CHANGELOG.md.",
    )
    parser.add_argument(
        "--version",
        help="Override project version; primarily useful for tests.",
    )
    args = parser.parse_args()

    version = args.version or _load_project_version(args.root)
    changelog = args.root / "CHANGELOG.md"
    if not changelog.is_file():
        print("changelog check failed: CHANGELOG.md is missing", file=sys.stderr)
        return 1

    failures = _check_changelog(version, changelog.read_text(encoding="utf-8"))
    if failures:
        print("changelog check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(f"Changelog OK: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
