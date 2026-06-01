"""Ensure dependency maintenance automation stays enabled."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

UPDATE_RE = re.compile(r"^  - package-ecosystem:\s*(?P<name>[A-Za-z0-9_-]+)\s*$")


def _update_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current_name: str | None = None

    for line in text.splitlines():
        if match := UPDATE_RE.match(line):
            current_name = match.group("name")
            blocks[current_name] = [line]
            continue
        if current_name is not None:
            blocks[current_name].append(line)

    return {name: "\n".join(lines) for name, lines in blocks.items()}


def _contains_all(block: str, snippets: tuple[str, ...]) -> list[str]:
    return [snippet for snippet in snippets if snippet not in block]


def _check_config(text: str) -> list[str]:
    failures: list[str] = []
    if "version: 2" not in text.splitlines():
        failures.append("dependabot.yml must use version: 2")

    blocks = _update_blocks(text)
    required_blocks = {
        "pip": (
            '    directory: "/"',
            "      interval: weekly",
            "      day: monday",
            "    open-pull-requests-limit: 5",
            "      - dependencies",
            "      - python",
            "      dev-tools:",
            '          - "ruff"',
            '          - "mypy"',
            '          - "pytest*"',
            "      otel:",
            '          - "opentelemetry-*"',
        ),
        "github-actions": (
            '    directory: "/"',
            "      interval: weekly",
            "      day: monday",
            "    open-pull-requests-limit: 3",
            "      - dependencies",
            "      - github-actions",
        ),
    }

    for ecosystem, snippets in required_blocks.items():
        block = blocks.get(ecosystem)
        if block is None:
            failures.append(f"dependabot.yml is missing {ecosystem!r} updates")
            continue
        for snippet in _contains_all(block, snippets):
            failures.append(
                f"dependabot.yml {ecosystem!r} update is missing {snippet!r}"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(".github/dependabot.yml"),
        help="Dependency maintenance configuration to scan.",
    )
    args = parser.parse_args()

    if not args.path.is_file():
        print(f"maintenance check failed: {args.path} is missing", file=sys.stderr)
        return 1

    failures = _check_config(args.path.read_text(encoding="utf-8"))
    if failures:
        print("maintenance check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Maintenance check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
