"""Ensure the RC reviewer handoff remains complete and unsigned by default."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DOC = Path("docs/compliance/rc-review-handoff.md")
FINAL_REVIEW = Path("docs/compliance/final-diff-review.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
NAV_SNIPPET = "RC Review Handoff: compliance/rc-review-handoff.md"
WORKFLOW_SNIPPET = "python scripts/check_rc_review_handoff.py"

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Package Snapshot",
    "## Review Inputs",
    "## Diff Review Checklist",
    "## Gate Evidence Summary",
    "## External Blockers",
    "## No-Go Rules",
    "## Sign-Off Template",
)

DOC_REQUIRED_SNIPPETS = (
    "RC review handoff status: Pending until final reviewer signs off",
    "`techrevati-runtime`",
    "`techrevati.runtime`",
    "`0.3.0rc1`",
    "controlled internal",
    "public package index publication: out of scope until controlled pilot",
    "docs/compliance/rc-inventory.md",
    "docs/compliance/final-diff-review.md",
    "docs/compliance/staging-manifest.md",
    "docs/compliance/remote-ci-validation.md",
    "docs/compliance/security-review.md",
    "docs/compliance/private-rc-publication.md",
    "docs/compliance/pilot-evidence-template.md",
    "docs/compliance/rollback-proof-checklist.md",
    "git status --short --branch",
    "git ls-files --others --exclude-standard",
    "git diff --stat",
    "staging manifest guard",
    "untracked release assets are not classified",
    "public branding limited to Techrevati doo",
    "all verify-time guard results",
    "Current handoff snapshot collected",
    "branch:",
    "tracked diff:",
    "untracked release assets:",
    "Remote CI validation",
    "Controlled RC pilot",
    "Rollback proof",
    "Do not stage, tag, publish, pilot, or promote",
    "reviewer handoff is unsigned",
    "`techrevati.runtime` namespace changes",
    "Decision | Pending / Approved / Changes required",
)

FINAL_REVIEW_REQUIRED_SNIPPETS = (
    "docs/compliance/rc-review-handoff.md",
    "docs/compliance/staging-manifest.md",
    "reviewer handoff",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/rc-review-handoff.md",
    "docs/compliance/staging-manifest.md",
    "scripts/check_rc_review_handoff.py",
    "scripts/check_staging_manifest.py",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "reviewer handoff",
    "Final diff review | Open",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add an RC reviewer handoff checklist and guard.",
    "Add a staging manifest checklist and guard.",
    "Final reviewer handoff may miss blockers | Mitigated",
    "Untracked RC assets may be missed during staging | Mitigated",
    "RC reviewer handoff guard passed.",
    "Staging manifest guard passed.",
)

BRANCH_RE = re.compile(r"branch:\s*`(?P<branch>[^`]+)`", re.IGNORECASE)
TRACKED_DIFF_RE = re.compile(
    r"tracked diff:\s*"
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
DiffStats = tuple[int, int, int]


def _missing_snippets(text: str, snippets: tuple[str, ...]) -> list[str]:
    text_lower = text.lower()
    return [snippet for snippet in snippets if snippet.lower() not in text_lower]


def _check_text_file(
    root: Path,
    path: Path,
    *,
    groups: tuple[tuple[str, tuple[str, ...]], ...],
) -> list[str]:
    full_path = root / path
    if not full_path.is_file():
        return [f"{path.as_posix()} is missing"]
    text = full_path.read_text(encoding="utf-8")
    failures: list[str] = []
    for label, snippets in groups:
        for snippet in _missing_snippets(text, snippets):
            failures.append(f"{path.as_posix()} is missing {label}: {snippet}")
    return failures


def _check_nav(root: Path) -> list[str]:
    path = root / "mkdocs.yml"
    if not path.is_file():
        return ["mkdocs.yml is missing"]
    if NAV_SNIPPET not in path.read_text(encoding="utf-8"):
        return [f"mkdocs.yml is missing RC review handoff nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if WORKFLOW_SNIPPET not in path.read_text(encoding="utf-8"):
            failures.append(f"{workflow} is missing RC review handoff guard")
    return failures


def _count(value: str | None) -> int:
    if value is None:
        return 0
    return int(value.replace(",", ""))


def _documented_branch(text: str) -> str | None:
    match = BRANCH_RE.search(text)
    if match is None:
        return None
    return match.group("branch")


def _documented_diff_stats(text: str) -> DiffStats | None:
    match = TRACKED_DIFF_RE.search(text)
    if match is None:
        return None
    return (
        _count(match.group("files")),
        _count(match.group("insertions")),
        _count(match.group("deletions")),
    )


def _documented_untracked_count(text: str) -> int | None:
    match = UNTRACKED_RE.search(text)
    if match is None:
        return None
    return _count(match.group("count"))


def _git_branch(root: Path) -> tuple[str | None, list[str]]:
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


def _git_diff_stats(root: Path) -> tuple[DiffStats | None, list[str]]:
    try:
        result = subprocess.run(
            ["git", "diff", "--shortstat"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return None, [f"could not inspect tracked diff: {exc}"]
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or str(exc)
        return None, [f"could not inspect tracked diff: {details}"]

    output = result.stdout.strip()
    if not output:
        return (0, 0, 0), []
    match = GIT_SHORTSTAT_RE.fullmatch(output)
    if match is None:
        return None, [f"could not parse git diff --shortstat output: {output}"]
    return (
        _count(match.group("files")),
        _count(match.group("insertions")),
        _count(match.group("deletions")),
    ), []


def _git_untracked_count(root: Path) -> tuple[int | None, list[str]]:
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


def _check_snapshot_parity(root: Path) -> list[str]:
    if not (root / ".git").exists():
        return []

    doc_path = root / DOC
    if not doc_path.is_file():
        return []
    text = doc_path.read_text(encoding="utf-8")
    failures: list[str] = []

    documented_branch = _documented_branch(text)
    if documented_branch is None:
        failures.append("RC review handoff is missing branch snapshot")
    current_branch, branch_failures = _git_branch(root)
    failures.extend(branch_failures)
    if documented_branch is not None and current_branch is not None:
        if documented_branch != current_branch:
            failures.append(
                "RC review handoff branch snapshot drift: "
                f"documents {documented_branch}, current is {current_branch}"
            )

    documented_stats = _documented_diff_stats(text)
    if documented_stats is None:
        failures.append("RC review handoff is missing tracked diff snapshot")
    current_stats, stat_failures = _git_diff_stats(root)
    failures.extend(stat_failures)
    if documented_stats is not None and current_stats is not None:
        if documented_stats != current_stats:
            failures.append(
                "RC review handoff tracked diff snapshot drift: "
                f"documents {documented_stats}, current is {current_stats}"
            )

    documented_untracked = _documented_untracked_count(text)
    if documented_untracked is None:
        failures.append("RC review handoff is missing untracked asset snapshot")
    current_untracked, untracked_failures = _git_untracked_count(root)
    failures.extend(untracked_failures)
    if documented_untracked is not None and current_untracked is not None:
        if documented_untracked != current_untracked:
            failures.append(
                "RC review handoff untracked asset snapshot drift: "
                f"documents {documented_untracked}, current is {current_untracked}"
            )

    return failures


def _failures(root: Path) -> list[str]:
    failures = _check_text_file(
        root,
        DOC,
        groups=(
            ("section", REQUIRED_SECTIONS),
            ("handoff control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(
        _check_text_file(
            root,
            FINAL_REVIEW,
            groups=(("handoff pointer", FINAL_REVIEW_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("handoff pointer", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("handoff boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("handoff inventory", INVENTORY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(_check_snapshot_parity(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing RC review handoff docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("RC review handoff check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("RC review handoff check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
