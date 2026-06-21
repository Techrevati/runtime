"""Ensure remote CI validation evidence stays explicit and commit-bound."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import release_preflight

DOC = Path("docs/compliance/remote-ci-validation.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
NAV_SNIPPET = "Remote CI Validation: compliance/remote-ci-validation.md"
WORKFLOW_SNIPPET = "python scripts/check_remote_ci_validation.py"

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Preflight Snapshot",
    "## Commit Parity",
    "## Required Jobs",
    "## Evidence To Capture",
    "## Failure Triage",
    "## No-Go Rules",
    "## Sign-Off Template",
)

DOC_REQUIRED_SNIPPETS = (
    "Remote CI validation status: Pending until remote CI passes",
    "base HEAD before staging",
    "current working-tree release diff",
    "untracked release assets",
    "This is preflight evidence only",
    "reviewed release-candidate commit",
    "reviewed commit SHA",
    "workflow run commit",
    "If the branch changes after remote CI passes",
    "test matrix for Python 3.11, 3.12, and 3.13",
    "build matrix for Python 3.11, 3.12, and 3.13",
    "zero-dependency wheel smoke",
    "all verify-time guard scripts",
    "documentation build and public branding checks",
    "run URL or immutable run identifier",
    "product defect",
    "workflow defect",
    "dependency or toolchain outage",
    "remote CI ran on a different commit",
    "Decision | Pending / Approved / Changes required",
)

CI_WORKFLOW_REQUIRED_SNIPPETS = (
    "python-version: ['3.11', '3.12', '3.13']",
    "python scripts/check_remote_ci_validation.py",
    "python scripts/check_module_coverage.py --threshold 88",
    "pip install --no-index --no-deps --find-links dist techrevati-runtime",
    "zero-deps-smoke",
)

RELEASE_WORKFLOW_REQUIRED_SNIPPETS = (
    "python-version: ['3.11', '3.12', '3.13']",
    "python scripts/check_remote_ci_validation.py",
    "python scripts/check_release_tag.py",
    "python scripts/check_distribution.py dist",
    "python -m twine check dist/*.whl dist/*.tar.gz",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/remote-ci-validation.md",
    "scripts/check_remote_ci_validation.py",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "remote CI validation checklist",
    "Remote CI validation | Open",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add a remote CI validation checklist and guard.",
    "Remote CI evidence may mismatch the reviewed commit | Mitigated",
    "Remote CI validation guard passed.",
)


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
        return [f"mkdocs.yml is missing remote CI validation nav: {NAV_SNIPPET}"]
    return []


def _check_workflow(
    root: Path,
    workflow: str,
    snippets: tuple[str, ...],
) -> list[str]:
    path = root / ".github" / "workflows" / workflow
    if not path.is_file():
        return [f"{workflow} is missing"]
    text = path.read_text(encoding="utf-8")
    return [
        f"{workflow} is missing remote CI validation control: {snippet}"
        for snippet in _missing_snippets(text, snippets)
    ]


_git_branch = release_preflight.git_branch
_git_head = release_preflight.git_head
_git_diff_stats = release_preflight.git_diff_stats
_git_untracked_count = release_preflight.git_untracked_count


def _check_preflight_snapshot_parity(root: Path) -> list[str]:
    return release_preflight.check_preflight_snapshot_parity(
        root,
        doc=DOC,
        label="remote CI validation",
        branch_inspector=_git_branch,
        head_inspector=_git_head,
        diff_inspector=_git_diff_stats,
        untracked_inspector=_git_untracked_count,
    )


def _failures(root: Path) -> list[str]:
    failures = _check_text_file(
        root,
        DOC,
        groups=(
            ("section", REQUIRED_SECTIONS),
            ("remote CI control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflow(root, "ci.yml", CI_WORKFLOW_REQUIRED_SNIPPETS))
    failures.extend(
        _check_workflow(root, "release.yml", RELEASE_WORKFLOW_REQUIRED_SNIPPETS)
    )
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("remote CI validation pointer", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("remote CI validation boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("remote CI validation inventory", INVENTORY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(_check_preflight_snapshot_parity(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing remote CI validation docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("remote CI validation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Remote CI validation check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
