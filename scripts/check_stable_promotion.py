"""Ensure stable promotion cannot bypass external release evidence."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import release_preflight

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]

DOC = Path("docs/compliance/stable-promotion.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
CHANGELOG = Path("CHANGELOG.md")
NAV_SNIPPET = "Stable Promotion: compliance/stable-promotion.md"
WORKFLOW_SNIPPET = "python scripts/check_stable_promotion.py"
STABLE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Stable Promotion Preflight Snapshot",
    "## Promotion Boundary",
    "## Required Evidence",
    "## Evidence Freshness",
    "## Stable Go Conditions",
    "## Stable No-Go Rules",
    "## Approval Record Template",
    "## Post-Promotion Controls",
)

DOC_REQUIRED_SNIPPETS = (
    "Stable promotion status:",
    "Latest stable promotion preflight snapshot collected",
    "branch:",
    "base HEAD before staging",
    "current package version: `0.3.0rc1`",
    "target stable version: `0.3.0`",
    "current working-tree release diff",
    "untracked release assets",
    "final diff review, staging manifest, and RC review handoff snapshot parity",
    "security, private RC publication, pilot execution, and rollback execution",
    "release artifact preflight",
    "stable promotion guard: passed for the RC version while status remains",
    "stable `0.3.0` promotion: Blocked until final reviewer sign-off",
    "zero P0 incidents",
    "zero unresolved P1 incidents",
    "preflight evidence only",
    "Do not promote",
    "same commit",
    "final diff review checklist approved",
    "RC reviewer handoff approved",
    "remote CI validation checklist approved",
    "security review approved",
    "private RC publication evidence captured",
    "controlled RC pilot evidence template completed",
    "rollback execution checklist completed",
    "rollback proof checklist completed",
    "public branding guard passed",
    "SHA256SUMS evidence",
    "secret leak guard passed",
    "dependency vulnerability guard passed",
    "zero P0 incidents",
    "zero unresolved P1 incidents",
    "If the branch changes after any approval",
    "public package index publication remains out of scope until pilot approval",
    "remote CI validation is missing or stale",
    "controlled RC pilot evidence is incomplete",
    "rollback execution or rollback proof is incomplete",
    "Decision | Pending / Approved / Changes required",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/stable-promotion.md",
    "scripts/check_stable_promotion.py",
    "stable promotion record",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "stable promotion record",
    "Stable promotion | Open",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add a stable promotion checklist and guard.",
    "Stable promotion may start before external evidence is complete | Mitigated",
    "Stable promotion guard passed.",
)

CHANGELOG_REQUIRED_SNIPPETS = ("Stable promotion checklist and guard",)

APPROVED_PROMOTION_SNIPPETS = (
    "Stable promotion status: Approved",
    "| Remote CI validation | Approved |",
    "| Security review | Approved |",
    "| Private RC publication evidence | Approved |",
    "| Controlled RC pilot evidence | Approved |",
    "| Rollback execution proof | Approved |",
    "| P0 incidents | 0 |",
    "| Unresolved P1 incidents | 0 |",
    "| Decision | Approved |",
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
        return [f"mkdocs.yml is missing stable promotion nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if WORKFLOW_SNIPPET not in path.read_text(encoding="utf-8"):
            failures.append(f"{workflow} is missing stable promotion guard")
    return failures


def _load_project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject: dict[str, Any] = tomllib.load(handle)
    return str(pyproject["project"]["version"])


def _check_stable_version_approval(root: Path, version: str) -> list[str]:
    if not STABLE_VERSION_RE.fullmatch(version):
        return []

    doc_path = root / DOC
    if not doc_path.is_file():
        return [f"{DOC.as_posix()} is missing"]

    text = doc_path.read_text(encoding="utf-8")
    return [
        f"stable version {version} requires approved stable promotion evidence: "
        f"{snippet}"
        for snippet in _missing_snippets(text, APPROVED_PROMOTION_SNIPPETS)
    ]


_git_branch = release_preflight.git_branch
_git_head = release_preflight.git_head
_git_diff_stats = release_preflight.git_diff_stats
_git_untracked_count = release_preflight.git_untracked_count


def _check_preflight_snapshot_parity(root: Path) -> list[str]:
    return release_preflight.check_preflight_snapshot_parity(
        root,
        doc=DOC,
        label="stable promotion",
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
            ("stable promotion control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("stable promotion pointer", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("stable promotion boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("stable promotion inventory", INVENTORY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            CHANGELOG,
            groups=(("stable promotion changelog entry", CHANGELOG_REQUIRED_SNIPPETS),),
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
        help="Project root containing stable promotion docs and workflows.",
    )
    parser.add_argument(
        "--version",
        help="Override project version; primarily useful for tests.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    version = args.version or _load_project_version(args.root)
    failures.extend(_check_stable_version_approval(args.root, version))
    if failures:
        print("stable promotion check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Stable promotion check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
