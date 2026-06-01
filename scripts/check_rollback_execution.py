"""Ensure rollback execution proof cannot be replaced by command-shape checks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import release_preflight

DOC = Path("docs/compliance/rollback-execution.md")
ROLLBACK_PROOF = Path("docs/compliance/rollback-proof-checklist.md")
PILOT_EXECUTION = Path("docs/compliance/pilot-execution.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
NAV_SNIPPET = "Rollback Execution Checklist: compliance/rollback-execution.md"
WORKFLOW_SNIPPET = "python scripts/check_rollback_execution.py"

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Rollback Preflight Snapshot",
    "## Execution Boundary",
    "## Preconditions",
    "## Evidence Preservation",
    "## Execution Steps",
    "## Verification Commands",
    "## Resume And Checkpoint Proof",
    "## Failure Handling",
    "## No-Go Rules",
    "## Sign-Off Template",
)

DOC_REQUIRED_SNIPPETS = (
    "Rollback execution status: Pending until rollback is proven",
    "Latest rollback preflight snapshot collected",
    "branch:",
    "base HEAD before staging",
    "current working-tree release diff",
    "untracked release assets",
    "rollback command shape: documented with `--no-index`, `--no-deps`",
    "local pilot dry-run rollback readiness command shape: passed",
    "rollback execution guard: passed",
    "pilot execution guard: passed",
    "previous known-good version",
    "operator sign-off: Pending",
    "preflight evidence only",
    "do not prove rollback",
    "previous known-good runtime package",
    "downstream pilot environment installs the previous known-good version",
    "previous known-good wheel and source archive",
    "private artifact source",
    "`events.db`, `usage.db`, `checkpoints.db`, and process logs are preserved",
    "without a public package index fallback",
    "remote CI validation evidence",
    "private RC publication evidence",
    "python -m pip install --no-index --no-deps --find-links",
    "TECHREVATI_RUNTIME_ROLLBACK_VERSION",
    "importlib.metadata.version('techrevati-runtime')",
    "downstream worker starts",
    "one successful session completes",
    "event sink writes a new event",
    "usage sink writes a new usage record",
    "resume from a checkpoint created before rollback",
    "command-shape proof is the only rollback evidence",
    "checkpoint behavior is unknown",
    "Decision | Pending / Approved / Changes required",
)

ROLLBACK_PROOF_REQUIRED_SNIPPETS = (
    "docs/compliance/rollback-execution.md",
    "real rollback execution",
)

PILOT_EXECUTION_REQUIRED_SNIPPETS = (
    "docs/compliance/rollback-execution.md",
    "rollback execution",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/rollback-execution.md",
    "scripts/check_rollback_execution.py",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "rollback execution checklist",
    "Rollback proof | Open",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add a rollback execution checklist and guard.",
    "Rollback is not yet proven in pilot environment | Open",
    "guarded rollback execution checklist",
    "Rollback execution guard passed.",
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
        return [f"mkdocs.yml is missing rollback execution nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if WORKFLOW_SNIPPET not in path.read_text(encoding="utf-8"):
            failures.append(f"{workflow} is missing rollback execution guard")
    return failures


_git_branch = release_preflight.git_branch
_git_head = release_preflight.git_head
_git_diff_stats = release_preflight.git_diff_stats
_git_untracked_count = release_preflight.git_untracked_count


def _check_preflight_snapshot_parity(root: Path) -> list[str]:
    return release_preflight.check_preflight_snapshot_parity(
        root,
        doc=DOC,
        label="rollback execution",
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
            ("rollback execution control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(
        _check_text_file(
            root,
            ROLLBACK_PROOF,
            groups=(("rollback execution pointer", ROLLBACK_PROOF_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            PILOT_EXECUTION,
            groups=(("rollback execution pointer", PILOT_EXECUTION_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("rollback execution pointer", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("rollback execution boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("rollback execution inventory", INVENTORY_REQUIRED_SNIPPETS),),
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
        help="Project root containing rollback execution docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("rollback execution check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Rollback execution check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
