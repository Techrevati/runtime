"""Ensure the release-candidate security review remains explicit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import release_preflight

DOC = Path("docs/compliance/security-review.md")
SECURITY = Path("SECURITY.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
NAV_SNIPPET = "Security Review: compliance/security-review.md"
WORKFLOW_SNIPPET = "python scripts/check_security_review.py"

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Security Preflight Snapshot",
    "## Review Scope",
    "## Required Evidence",
    "## Runtime Risk Review",
    "## Supply Chain Review",
    "## Secret And Data Exposure Review",
    "## Workflow And Release Review",
    "## Pilot Security Controls",
    "## No-Go Rules",
    "## Reviewer Sign-Off Template",
)

DOC_REQUIRED_SNIPPETS = (
    "Security review status: Pending until reviewer signs off",
    "library, not a service",
    "in-process dependency",
    "Latest security preflight snapshot collected",
    "base HEAD before staging",
    "current working-tree release diff",
    "untracked release assets",
    "secret leak guard: passed",
    "dependency vulnerability guard: passed",
    "security pattern guard: passed",
    "workflow action pinning guard: passed",
    "workflow hardening guard: passed",
    "release workflow guard: passed",
    "private RC publication guard: passed",
    "public branding guard: passed",
    "package metadata rendering with `twine check`: passed",
    "local SBOM JSON, SBOM XML, and `SHA256SUMS` generation: passed",
    "release evidence guard against local `dist`: passed",
    "remote CI and security reviewer sign-off: Pending",
    "preflight evidence only",
    "model output is untrusted",
    "tool implementations run with caller process privileges",
    "`PermissionEnforcer` is a policy gate, not a sandbox",
    "guardrails reduce risk but do not isolate tool bodies",
    "runtime required dependency set remains empty",
    "critical findings",
    "secret leak guard output",
    "dependency vulnerability guard output",
    "security pattern guard output",
    "workflow action pinning guard output",
    "workflow hardening guard output",
    "private RC publication guard output",
    "public branding guard output",
    "built-in model I/O logging is metadata-only by default",
    "OTel event detail export is opt-in",
    "caller-driven cancellation classifies as cancellation rather than unknown",
    "caller-driven cancellation remains visible in telemetry without being marked",
    "public package index publication is out of scope until pilot approval",
    "rollback target is unknown",
    "Decision | Pending / Approved / Changes required",
)

SECURITY_REQUIRED_SNIPPETS = (
    "## Threat Model",
    "## Deployment Threat Model",
    "## Release Artifact Verification",
    "docs/compliance/security-review.md",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/security-review.md",
    "scripts/check_security_review.py",
    "caller-driven cancellation does not set OTel `error.type`",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "security review checklist",
    "security review checklist is complete and signed off before private RC",
    "Security review | Open",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add a security review checklist and guard.",
    "Security review may miss runtime-specific risks | Mitigated",
    "Cancellation OTel non-error validation passed.",
    "Security review guard passed.",
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
        return [f"mkdocs.yml is missing security review nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if WORKFLOW_SNIPPET not in path.read_text(encoding="utf-8"):
            failures.append(f"{workflow} is missing security review guard")
    return failures


_git_branch = release_preflight.git_branch
_git_head = release_preflight.git_head
_git_diff_stats = release_preflight.git_diff_stats
_git_untracked_count = release_preflight.git_untracked_count


def _check_preflight_snapshot_parity(root: Path) -> list[str]:
    return release_preflight.check_preflight_snapshot_parity(
        root,
        doc=DOC,
        label="security review",
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
            ("security review control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(
        _check_text_file(
            root,
            SECURITY,
            groups=(("security policy pointer", SECURITY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("security review pointer", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("security review boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("security review inventory", INVENTORY_REQUIRED_SNIPPETS),),
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
        help="Project root containing security review docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("security review check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Security review check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
