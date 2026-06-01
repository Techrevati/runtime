"""Require baseline hardening controls in CI workflows."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

JOB_RE = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*$")
TIMEOUT_RE = re.compile(r"^    timeout-minutes:\s*(?P<value>\d+)\s*$")
MAX_TIMEOUT_MINUTES = 30


def _workflow_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for pattern in ("*.yml", "*.yaml"):
        yield from sorted(root.rglob(pattern))


def _has_top_level_key(lines: list[str], key: str) -> bool:
    return any(line == f"{key}:" for line in lines)


def _job_blocks(lines: list[str]) -> Iterable[tuple[str, list[str]]]:
    in_jobs = False
    current_name: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if line == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line.startswith(" "):
            break

        if match := JOB_RE.match(line):
            if current_name is not None:
                yield current_name, current_lines
            current_name = match.group("name")
            current_lines = []
            continue

        if current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        yield current_name, current_lines


def _checkout_step_blocks(lines: list[str]) -> Iterable[list[str]]:
    for index, line in enumerate(lines):
        if "uses: actions/checkout@" not in line:
            continue
        block = [line]
        for next_line in lines[index + 1 :]:
            if next_line.startswith("      - "):
                break
            block.append(next_line)
        yield block


def _check_workflow(path: Path, text: str) -> list[str]:
    lines = text.splitlines()
    failures: list[str] = []

    if any(line.strip() == "pull_request_target:" for line in lines):
        failures.append(f"{path}: pull_request_target is not allowed")

    for key in ("permissions", "concurrency"):
        if not _has_top_level_key(lines, key):
            failures.append(f"{path}: missing top-level {key}: block")

    for block in _checkout_step_blocks(lines):
        if not any(line.strip() == "persist-credentials: false" for line in block):
            failures.append(
                f"{path}: checkout step must set persist-credentials: false"
            )

    jobs = list(_job_blocks(lines))
    if not jobs:
        failures.append(f"{path}: no jobs found")

    for job_name, job_lines in jobs:
        timeout_lines = [
            line for line in job_lines if line.startswith("    timeout-minutes:")
        ]
        if not timeout_lines:
            failures.append(f"{path}: job {job_name!r} is missing timeout-minutes")
            continue
        if len(timeout_lines) > 1:
            failures.append(f"{path}: job {job_name!r} has duplicate timeout-minutes")
            continue
        match = TIMEOUT_RE.match(timeout_lines[0])
        if match is None:
            failures.append(f"{path}: job {job_name!r} has invalid timeout-minutes")
            continue
        timeout = int(match.group("value"))
        if timeout < 1 or timeout > MAX_TIMEOUT_MINUTES:
            failures.append(
                f"{path}: job {job_name!r} timeout-minutes must be "
                f"between 1 and {MAX_TIMEOUT_MINUTES}"
            )

    return failures


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

    failures: list[str] = []
    for path in _workflow_files(args.path):
        failures.extend(_check_workflow(path, path.read_text(encoding="utf-8")))

    if failures:
        print("workflow hardening check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Workflow hardening check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
