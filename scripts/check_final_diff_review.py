"""Ensure final RC diff review guidance stays complete and honest."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REVIEW_DOC = Path("docs/compliance/final-diff-review.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
NAV_SNIPPET = "Final Diff Review: compliance/final-diff-review.md"

REQUIRED_SECTIONS = (
    "## Review Purpose",
    "## Review Scope",
    "## Review Order",
    "## Current Pre-Review Evidence Snapshot",
    "## Subsystem Review Matrix",
    "## Mandatory No-Go Rules",
    "## Evidence To Attach",
    "## Reviewer Sign-Off Template",
)

REQUIRED_SUBSYSTEMS = (
    "Repository hygiene",
    "Branding and authorship",
    "Package metadata",
    "Workflows and automation",
    "Guard scripts and tests",
    "Public API compatibility",
    "Runtime behavior",
    "Documentation",
    "Examples and migrations",
)

REQUIRED_NO_GO_RULES = (
    "public branding contains anything other than Techrevati doo",
    "technical namespace `techrevati.runtime` is changed",
    "package-level public API exports differ from the frozen set",
    "documented public imports disagree with the frozen public API",
    "workflow action is unpinned",
    "secret leak guard fails",
    "dependency vulnerability guard has unresolved high or critical findings",
    "public package index publication is enabled before pilot approval",
    "full production gate fails",
    "generated artifacts or local caches are included in the release diff",
    "staging manifest has unclassified untracked files",
    "pilot evidence or rollback proof is marked complete before the real pilot",
    "remote CI is missing when tagging or publishing",
)

REQUIRED_EVIDENCE = (
    "git status --short --branch",
    "git ls-files --others --exclude-standard",
    "git diff --stat",
    "full production gate output",
    "remote CI result for the same commit",
    "wheel and sdist verification",
    "SBOM JSON and XML verification",
    "public branding guard output",
    "secret leak guard output",
    "dependency vulnerability guard output",
    "RC readiness summary",
    "staging manifest checklist",
    "pilot dry-run output",
    "open stable-release blocker list",
)

REQUIRED_STATUS_SNIPPETS = (
    "Final diff review status: Pending until reviewer signs off",
    "Decision | Pending / Approved / Changes required",
    "It does not approve stable `0.3.0`",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Large diff is hard to review | Mitigated",
    "Guarded final diff review checklist and subsystem review order",
    "Review Order",
    "Full production gate evidence",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "final diff review",
    "not stable production-ready",
)

STAGING_REQUIRED_SNIPPETS = (
    "docs/compliance/staging-manifest.md",
    "staging manifest",
)

SNAPSHOT_REQUIRED_SNIPPETS = (
    "tracked diff:",
    "untracked release assets:",
)
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
    mkdocs = root / "mkdocs.yml"
    if not mkdocs.is_file():
        return ["mkdocs.yml is missing"]
    if NAV_SNIPPET not in mkdocs.read_text(encoding="utf-8"):
        return [f"mkdocs.yml is missing final diff review nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if "python scripts/check_final_diff_review.py" not in path.read_text(
            encoding="utf-8"
        ):
            failures.append(f"{workflow} is missing final diff review guard")
    return failures


def _count(value: str | None) -> int:
    if value is None:
        return 0
    return int(value.replace(",", ""))


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


def _git_diff_stats(root: Path) -> tuple[DiffStats | None, list[str]]:
    try:
        result = subprocess.run(
            ["git", "diff", "main...HEAD", "--shortstat"],
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

    doc_path = root / REVIEW_DOC
    if not doc_path.is_file():
        return []
    text = doc_path.read_text(encoding="utf-8")
    failures: list[str] = []

    documented_stats = _documented_diff_stats(text)
    if documented_stats is None:
        failures.append("final diff review is missing tracked diff snapshot")
    current_stats, stat_failures = _git_diff_stats(root)
    failures.extend(stat_failures)
    if documented_stats is not None and current_stats is not None:
        if documented_stats != current_stats:
            failures.append(
                "final diff review tracked diff snapshot drift: "
                f"documents {documented_stats}, current is {current_stats}"
            )

    documented_untracked = _documented_untracked_count(text)
    if documented_untracked is None:
        failures.append("final diff review is missing untracked asset snapshot")
    current_untracked, untracked_failures = _git_untracked_count(root)
    failures.extend(untracked_failures)
    if documented_untracked is not None and current_untracked is not None:
        if documented_untracked != current_untracked:
            failures.append(
                "final diff review untracked asset snapshot drift: "
                f"documents {documented_untracked}, current is {current_untracked}"
            )

    return failures


def _failures(root: Path) -> list[str]:
    failures = _check_text_file(
        root,
        REVIEW_DOC,
        groups=(
            ("section", REQUIRED_SECTIONS),
            ("subsystem", REQUIRED_SUBSYSTEMS),
            ("no-go rule", REQUIRED_NO_GO_RULES),
            ("evidence item", REQUIRED_EVIDENCE),
            ("status boundary", REQUIRED_STATUS_SNIPPETS),
            ("staging manifest pointer", STAGING_REQUIRED_SNIPPETS),
            ("pre-review snapshot", SNAPSHOT_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("inventory review pointer", INVENTORY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("RC readiness boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(_check_snapshot_parity(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing final diff review docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("final diff review check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Final diff review check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
