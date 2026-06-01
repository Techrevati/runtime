"""Ensure private release-candidate publication stays controlled."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import release_preflight

DOC = Path("docs/compliance/private-rc-publication.md")
PLAN = Path("docs/compliance/production-readiness.md")
SUMMARY = Path("docs/compliance/rc-readiness-summary.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
NAV_SNIPPET = "Private RC Publication: compliance/private-rc-publication.md"
WORKFLOW_SNIPPET = "python scripts/check_private_rc_publication.py"

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Publication Preflight Snapshot",
    "## Publication Boundary",
    "## Required Inputs",
    "## Artifact Verification",
    "## Private Channel Controls",
    "## Credential Handling",
    "## Publication Procedure",
    "## No-Go Rules",
    "## Evidence Template",
)

DOC_REQUIRED_SNIPPETS = (
    "Private RC publication status: Pending until `0.3.0rc1` is published",
    "Latest private publication preflight snapshot collected",
    "base HEAD before staging",
    "current working-tree release diff",
    "untracked release assets",
    "fresh wheel and source archive build for `0.3.0rc1`: passed",
    "package metadata rendering with explicit wheel and source archive",
    "local SBOM JSON, SBOM XML, and `SHA256SUMS` generation: passed",
    "release evidence guard against local `dist`: passed",
    "temporary private-package upload staging with wheel and source archive only",
    "private-package staged artifact distribution and metadata checks: passed",
    "private registry secret validation",
    "preflight evidence only",
    "Public package index publication is out of scope until pilot approval",
    "`techrevati_runtime-0.3.0rc1-py3-none-any.whl`",
    "`techrevati_runtime-0.3.0rc1.tar.gz`",
    "`sbom.cyclonedx.json`",
    "`sbom.cyclonedx.xml`",
    "`SHA256SUMS`",
    "python scripts/check_distribution.py dist",
    "python scripts/check_release_evidence.py dist",
    "python -m twine check dist/*.whl dist/*.tar.gz",
    "pip install --force-reinstall --no-index --no-deps --find-links dist "
    "techrevati-runtime",
    "(cd dist && sha256sum -c SHA256SUMS)",
    "approved security review with no unresolved high or critical findings",
    "security review is unsigned",
    "must contain only wheel and source archive files",
    "SBOM files and `SHA256SUMS` stay attached",
    "private repository URL, username, and password/token",
    "must never be committed, printed in logs, stored in release",
    "workflow falls back to a default public package index",
    "rollback target is not known",
    "Decision | Pending / Approved / Changes required",
)

DOC_FORBIDDEN_LINES = ("sha256sum -c SHA256SUMS",)

RELEASE_WORKFLOW_REQUIRED_SNIPPETS = (
    "python scripts/check_private_rc_publication.py",
    "mkdir -p private-package-dist",
    "cp dist/*.whl dist/*.tar.gz private-package-dist/",
    "python scripts/check_distribution.py private-package-dist",
    "python -m twine check private-package-dist/*.whl private-package-dist/*.tar.gz",
    "pip install --force-reinstall --no-index --no-deps --find-links dist "
    "techrevati-runtime",
    "sha256sum *.whl *.tar.gz sbom.cyclonedx.json sbom.cyclonedx.xml > SHA256SUMS",
    "python scripts/check_release_evidence.py dist",
    "dist/SHA256SUMS",
    "Check private package repository configuration",
    "PRIVATE_PACKAGE_REPOSITORY_URL: ${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}",
    "PRIVATE_PACKAGE_USERNAME: ${{ secrets.PRIVATE_PACKAGE_USERNAME }}",
    "PRIVATE_PACKAGE_PASSWORD: ${{ secrets.PRIVATE_PACKAGE_PASSWORD }}",
    'test -n "$PRIVATE_PACKAGE_REPOSITORY_URL"',
    'test -n "$PRIVATE_PACKAGE_USERNAME"',
    'test -n "$PRIVATE_PACKAGE_PASSWORD"',
    "Publish private package",
    "packages-dir: private-package-dist",
    "repository-url: ${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}",
    "user: ${{ secrets.PRIVATE_PACKAGE_USERNAME }}",
    "password: ${{ secrets.PRIVATE_PACKAGE_PASSWORD }}",
)

RELEASE_WORKFLOW_FORBIDDEN_SNIPPETS = (
    "id-token: write",
    "packages-dir: pypi-dist",
    "__token__",
    "api-token:",
    "repository-url: https://upload." + "pypi.org",
    "repository-url: https://test." + "pypi.org",
    "pip install --no-index --no-deps --find-links dist techrevati-runtime",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/private-rc-publication.md",
    "scripts/check_private_rc_publication.py",
)

SUMMARY_REQUIRED_SNIPPETS = (
    "private RC publication checklist",
    "private package repository URL",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add a private RC publication checklist and guard.",
    "Private RC publication may publish wrong artifacts or channel | Mitigated",
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


def _check_doc(root: Path) -> list[str]:
    failures = _check_text_file(
        root,
        DOC,
        groups=(
            ("section", REQUIRED_SECTIONS),
            ("publication control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    path = root / DOC
    if path.is_file():
        lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
        failures.extend(
            f"{DOC.as_posix()} contains root-relative publication command: {line}"
            for line in DOC_FORBIDDEN_LINES
            if line in lines
        )
    return failures


def _check_nav(root: Path) -> list[str]:
    path = root / "mkdocs.yml"
    if not path.is_file():
        return ["mkdocs.yml is missing"]
    if NAV_SNIPPET not in path.read_text(encoding="utf-8"):
        return [f"mkdocs.yml is missing private RC publication nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        if WORKFLOW_SNIPPET not in text:
            failures.append(f"{workflow} is missing private RC publication guard")

    release_path = root / ".github" / "workflows" / "release.yml"
    if not release_path.is_file():
        return failures
    release_text = release_path.read_text(encoding="utf-8")
    for snippet in _missing_snippets(
        release_text,
        RELEASE_WORKFLOW_REQUIRED_SNIPPETS,
    ):
        failures.append(f"release.yml is missing private publish control: {snippet}")
    for snippet in RELEASE_WORKFLOW_FORBIDDEN_SNIPPETS:
        if snippet in release_text:
            failures.append(
                f"release.yml contains forbidden publish control: {snippet}"
            )
    return failures


_git_branch = release_preflight.git_branch
_git_head = release_preflight.git_head
_git_diff_stats = release_preflight.git_diff_stats
_git_untracked_count = release_preflight.git_untracked_count


def _check_preflight_snapshot_parity(root: Path) -> list[str]:
    return release_preflight.check_preflight_snapshot_parity(
        root,
        doc=DOC,
        label="private RC publication",
        branch_inspector=_git_branch,
        head_inspector=_git_head,
        diff_inspector=_git_diff_stats,
        untracked_inspector=_git_untracked_count,
    )


def _failures(root: Path) -> list[str]:
    failures = _check_doc(root)
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("private RC publication pointer", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            SUMMARY,
            groups=(("private RC publication boundary", SUMMARY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("private RC publication inventory", INVENTORY_REQUIRED_SNIPPETS),),
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
        help="Project root containing private RC publication docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("private RC publication check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Private RC publication check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
