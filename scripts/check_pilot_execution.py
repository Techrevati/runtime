"""Ensure the real pilot execution checklist is not confused with a dry-run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import release_preflight

DOC = Path("docs/compliance/pilot-execution.md")
PILOT_EVIDENCE = Path("docs/compliance/pilot-evidence-template.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
NAV_SNIPPET = "Pilot Execution Checklist: compliance/pilot-execution.md"
WORKFLOW_SNIPPET = "python scripts/check_pilot_execution.py"

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Pilot Preflight Snapshot",
    "## Dry-Run Boundary",
    "## Launch Preconditions",
    "## Pilot Window",
    "## Required Execution Scenarios",
    "## Required Evidence",
    "## Incident Handling",
    "## Go/No-Go Rules",
    "## Sign-Off Template",
)

DOC_REQUIRED_SNIPPETS = (
    "Pilot execution status: Pending until the real controlled pilot is complete",
    "Latest pilot preflight snapshot collected",
    "branch:",
    "base HEAD before staging",
    "current working-tree release diff",
    "untracked release assets",
    "pilot dry-run command: `python scripts/check_pilot_dry_run.py`",
    "pilot dry-run result: passed with 10 local scenarios",
    "pilot execution guard: passed",
    "rollback execution guard: passed",
    "real controlled pilot evidence, downstream telemetry",
    "preflight evidence only",
    "only pilot evidence",
    "`scripts/check_pilot_dry_run.py`",
    "Do not use local dry-run output as pilot evidence by itself",
    "does not replace the controlled pilot",
    "remote CI validation checklist is complete",
    "final reviewer handoff is complete",
    "security review checklist is complete",
    "private RC publication evidence is available",
    "pilot operations runbook has been reviewed",
    "rollback proof checklist has a recorded previous known-good target",
    "request-volume cap",
    "successful session",
    "prompt-injection attempt",
    "permission denial",
    "guardrail block",
    "provider failover",
    "checkpoint resume",
    "rollback to previous known-good version",
    "durable event records",
    "durable usage records",
    "token usage and estimated cost",
    "any P0 incident occurred",
    "any P1 incident remains unresolved",
    "local dry-run output is the only pilot evidence",
    "Decision | Pending / Approved / Changes required",
)

PILOT_EVIDENCE_REQUIRED_SNIPPETS = (
    "docs/compliance/pilot-execution.md",
    "real controlled pilot",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/pilot-execution.md",
    "scripts/check_pilot_execution.py",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "pilot execution checklist",
    "Controlled RC pilot | Open",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add a pilot execution checklist and guard.",
    "Local dry-run may be mistaken for real pilot evidence | Mitigated",
    "Pilot execution guard passed.",
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
        return [f"mkdocs.yml is missing pilot execution nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if WORKFLOW_SNIPPET not in path.read_text(encoding="utf-8"):
            failures.append(f"{workflow} is missing pilot execution guard")
    return failures


_git_branch = release_preflight.git_branch
_git_head = release_preflight.git_head
_git_diff_stats = release_preflight.git_diff_stats
_git_untracked_count = release_preflight.git_untracked_count


def _check_preflight_snapshot_parity(root: Path) -> list[str]:
    return release_preflight.check_preflight_snapshot_parity(
        root,
        doc=DOC,
        label="pilot execution",
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
            ("pilot execution control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(
        _check_text_file(
            root,
            PILOT_EVIDENCE,
            groups=(("pilot execution pointer", PILOT_EVIDENCE_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("pilot execution pointer", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("pilot execution boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("pilot execution inventory", INVENTORY_REQUIRED_SNIPPETS),),
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
        help="Project root containing pilot execution docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("pilot execution check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Pilot execution check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
