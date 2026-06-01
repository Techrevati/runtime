"""Shared release preflight snapshot parity helpers."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

DiffStats = tuple[int, int, int]
BranchInspector = Callable[[Path], tuple[str | None, list[str]]]
HeadInspector = Callable[[Path], tuple[str | None, list[str]]]
DiffInspector = Callable[[Path], tuple[DiffStats | None, list[str]]]
UntrackedInspector = Callable[[Path], tuple[int | None, list[str]]]

BRANCH_RE = re.compile(r"branch:\s*`(?P<branch>[^`]+)`", re.IGNORECASE)
BASE_HEAD_RE = re.compile(
    r"base HEAD before staging:\s*`(?P<head>[0-9a-f]{40})`",
    re.IGNORECASE,
)
TRACKED_DIFF_RE = re.compile(
    r"current working-tree release diff:\s*"
    r"(?P<files>[0-9,]+) files changed,\s*"
    r"(?P<insertions>[0-9,]+) insertions(?:\(\+\))?,\s*"
    r"(?P<deletions>[0-9,]+) deletions(?:\(-\))?",
    re.IGNORECASE,
)
UNTRACKED_RE = re.compile(
    r"untracked release assets:\s*(?P<count>[0-9,]+) files",
    re.IGNORECASE,
)
GIT_SHORTSTAT_RE = re.compile(
    r"(?:(?P<files>[0-9,]+) files? changed)?"
    r"(?:,\s*)?(?:(?P<insertions>[0-9,]+) insertions?\(\+\))?"
    r"(?:,\s*)?(?:(?P<deletions>[0-9,]+) deletions?\(-\))?"
)


def _count(value: str | None) -> int:
    if value is None:
        return 0
    return int(value.replace(",", ""))


def documented_branch(text: str) -> str | None:
    match = BRANCH_RE.search(text)
    if match is None:
        return None
    return match.group("branch")


def documented_base_head(text: str) -> str | None:
    match = BASE_HEAD_RE.search(text)
    if match is None:
        return None
    return match.group("head")


def documented_diff_stats(text: str) -> DiffStats | None:
    match = TRACKED_DIFF_RE.search(text)
    if match is None:
        return None
    return (
        _count(match.group("files")),
        _count(match.group("insertions")),
        _count(match.group("deletions")),
    )


def documented_untracked_count(text: str) -> int | None:
    match = UNTRACKED_RE.search(text)
    if match is None:
        return None
    return _count(match.group("count"))


def git_branch(root: Path) -> tuple[str | None, list[str]]:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return None, [f"could not inspect current branch: {exc}"]
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or str(exc)
        return None, [f"could not inspect current branch: {details}"]

    branch = result.stdout.strip()
    return branch or None, []


def git_head(root: Path) -> tuple[str | None, list[str]]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return None, [f"could not inspect HEAD: {exc}"]
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or str(exc)
        return None, [f"could not inspect HEAD: {details}"]

    return result.stdout.strip() or None, []


def git_diff_stats(root: Path) -> tuple[DiffStats | None, list[str]]:
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--shortstat"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return None, [f"could not inspect release diff: {exc}"]
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or str(exc)
        return None, [f"could not inspect release diff: {details}"]

    output = result.stdout.strip()
    if not output:
        return (0, 0, 0), []
    match = GIT_SHORTSTAT_RE.fullmatch(output)
    if match is None:
        return None, [f"could not parse git diff HEAD --shortstat output: {output}"]
    return (
        _count(match.group("files")),
        _count(match.group("insertions")),
        _count(match.group("deletions")),
    ), []


def git_untracked_count(root: Path) -> tuple[int | None, list[str]]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return None, [f"could not inspect untracked files: {exc}"]
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or str(exc)
        return None, [f"could not inspect untracked files: {details}"]

    return len(result.stdout.splitlines()), []


def check_preflight_snapshot_parity(
    root: Path,
    *,
    doc: Path,
    label: str,
    branch_inspector: BranchInspector = git_branch,
    head_inspector: HeadInspector = git_head,
    diff_inspector: DiffInspector = git_diff_stats,
    untracked_inspector: UntrackedInspector = git_untracked_count,
) -> list[str]:
    if not (root / ".git").exists():
        return []

    doc_path = root / doc
    if not doc_path.is_file():
        return []
    text = doc_path.read_text(encoding="utf-8")

    current_stats, stat_failures = diff_inspector(root)
    current_untracked, untracked_failures = untracked_inspector(root)
    failures = [*stat_failures, *untracked_failures]
    if failures:
        return failures

    if current_stats == (0, 0, 0) and current_untracked == 0:
        return []

    expected_branch = documented_branch(text)
    if expected_branch is None:
        failures.append(f"{label} is missing branch preflight snapshot")
    current_branch, branch_failures = branch_inspector(root)
    failures.extend(branch_failures)
    if expected_branch is not None and current_branch is not None:
        if expected_branch != current_branch:
            failures.append(
                f"{label} branch preflight drift: "
                f"documents {expected_branch}, current is {current_branch}"
            )

    expected_head = documented_base_head(text)
    if expected_head is None:
        failures.append(f"{label} is missing base HEAD preflight snapshot")
    current_head, head_failures = head_inspector(root)
    failures.extend(head_failures)
    if expected_head is not None and current_head is not None:
        if expected_head != current_head:
            failures.append(
                f"{label} base HEAD preflight drift: "
                f"documents {expected_head}, current is {current_head}"
            )

    expected_stats = documented_diff_stats(text)
    if expected_stats is None:
        failures.append(f"{label} is missing release diff snapshot")
    if expected_stats is not None and current_stats is not None:
        if expected_stats != current_stats:
            failures.append(
                f"{label} release diff preflight drift: "
                f"documents {expected_stats}, current is {current_stats}"
            )

    expected_untracked = documented_untracked_count(text)
    if expected_untracked is None:
        failures.append(f"{label} is missing untracked asset snapshot")
    if expected_untracked is not None and current_untracked is not None:
        if expected_untracked != current_untracked:
            failures.append(
                f"{label} untracked asset preflight drift: "
                f"documents {expected_untracked}, current is {current_untracked}"
            )

    return failures
