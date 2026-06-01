"""Ensure RC readiness docs keep production decision boundaries explicit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY_NAV = "RC Readiness Summary: compliance/rc-readiness-summary.md"

SUMMARY_REQUIRED_SECTIONS = (
    "## Current Status",
    "## Release Decision Boundaries",
    "## Private RC Go Conditions",
    "## Stable No-Go Conditions",
    "## Remaining Blockers",
    "## Evidence Checklist",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "not stable production-ready",
    "final diff review",
    "remote CI",
    "security review",
    "private RC publication",
    "controlled RC pilot",
    "rollback proof",
    "security review checklist is complete and signed off before private RC",
    "security review is approved with no unresolved high or critical findings",
    "public package index publication is explicitly out of scope",
    "zero P0 incidents",
    "zero unresolved P1 incidents",
    "dependency vulnerability guard",
    "completed pilot evidence template",
    "completed rollback proof checklist",
    "reviewer handoff checklist",
    "terminal failure-class evidence for policy, safety, quota, validation",
    "prompt rejection, cancellation, and runtime hard-stop outcomes",
)

SUMMARY_FORBIDDEN_SNIPPETS = (
    "security review checklist is complete or explicitly still pending",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "## Sprint 5 Preparation Checklist",
    "- [ ] Execute the real controlled RC pilot.",
    "- [ ] Test rollback in the pilot environment.",
    "RC has not run in a real pilot workflow",
    "Remote CI has not yet validated the final commit",
    "Rollback is not yet proven in pilot environment",
    "private RC publication, pilot evidence, and rollback validation",
    "Current Gate Evidence",
    "tests passed",
    "Total coverage",
    "Cancellation OTel non-error validation passed.",
)

PLAN_REQUIRED_SNIPPETS = (
    "The same commit is green in local/server gates and remote CI",
    "A private registry or controlled artifact channel is available",
    "A pilot workflow has completed without unresolved P0/P1 issues",
    "Operators have a runbook for monitoring, incident response, and rollback",
    "Require remote CI to pass on the release candidate commit",
    "Complete the guarded security review before creating the private RC tag",
    "Do not publish to a public package index until the controlled RC pilot has",
    "controlled RC pilot evidence template",
    "rollback proof checklist",
    "release-context evidence smoke",
    "python scripts/check_release_evidence.py dist",
    "SHA256SUMS",
    "SBOM JSON and XML generation",
    "caller-driven cancellation does not set OTel `error.type`",
)

WORKFLOW_REQUIRED_SNIPPETS = ("python scripts/check_rc_readiness.py",)


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
    if SUMMARY_NAV not in mkdocs.read_text(encoding="utf-8"):
        return [f"mkdocs.yml is missing RC readiness nav: {SUMMARY_NAV}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for snippet in _missing_snippets(text, WORKFLOW_REQUIRED_SNIPPETS):
            failures.append(f"{workflow} is missing RC readiness guard: {snippet}")
    return failures


def _check_forbidden_summary(root: Path) -> list[str]:
    path = root / SUMMARY
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    text_lower = text.lower()
    return [
        f"{SUMMARY.as_posix()} contains forbidden weakened boundary: {snippet}"
        for snippet in SUMMARY_FORBIDDEN_SNIPPETS
        if snippet.lower() in text_lower
    ]


def _failures(root: Path) -> list[str]:
    failures = _check_text_file(
        root,
        SUMMARY,
        groups=(
            ("section", SUMMARY_REQUIRED_SECTIONS),
            ("decision boundary", SUMMARY_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("release blocker", INVENTORY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("production criterion", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(_check_forbidden_summary(root))
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing RC readiness docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("RC readiness check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("RC readiness check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
