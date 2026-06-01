"""Ensure release staging scope is explicit before final stage/tag steps."""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

DOC = Path("docs/compliance/staging-manifest.md")
FINAL_REVIEW = Path("docs/compliance/final-diff-review.md")
HANDOFF = Path("docs/compliance/rc-review-handoff.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
PLAN = Path("docs/compliance/production-readiness.md")
GUARD_CALIBRATION = Path("docs/compliance/guard-calibration.md")
NAV_SNIPPET = "Staging Manifest: compliance/staging-manifest.md"
WORKFLOW_SNIPPET = "python scripts/check_staging_manifest.py"
CI_GUARDRAIL_ENTRY = '"check_staging_manifest.py"'

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Allowed Untracked Release Assets",
    "## Current Untracked Asset Snapshot",
    "## Generated Artifact Exclusions",
    "## Review Procedure",
    "## No-Go Rules",
    "## Sign-Off Template",
)

DOC_REQUIRED_SNIPPETS = (
    "Staging manifest status: Pending until final reviewer approves the staged",
    "git status --short --branch",
    "git ls-files --others --exclude-standard",
    "git diff --stat",
    "python scripts/check_staging_manifest.py",
    "docs/api/*.md",
    "docs/compliance/*.md",
    "The staging manifest guard compares these counts against the current",
    "docs/styles/*.css",
    "docs_theme/*.html",
    "scripts/check_*.py",
    "scripts/release_preflight.py",
    "scripts/install_toolchain.py",
    "scripts/mkdocs_hooks/*.py",
    "src/techrevati/runtime/pilot.py",
    "tests/test_*.py",
    ".venv",
    "dist",
    "site",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".coverage",
    "Decision | Pending / Approved / Changes required",
)

FINAL_REVIEW_REQUIRED_SNIPPETS = (
    "docs/compliance/staging-manifest.md",
    "git ls-files --others --exclude-standard",
    "staging manifest has unclassified untracked files",
)

HANDOFF_REQUIRED_SNIPPETS = (
    "docs/compliance/staging-manifest.md",
    "staging manifest guard",
    "untracked release assets are not classified",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "Add a staging manifest checklist and guard.",
    "Untracked RC assets may be missed during staging | Mitigated",
    "Staging manifest guard passed.",
)

PLAN_REQUIRED_SNIPPETS = (
    "docs/compliance/staging-manifest.md",
    "scripts/check_staging_manifest.py",
)

GUARD_CALIBRATION_REQUIRED_SNIPPETS = (
    "`check_staging_manifest.py`",
    "staging manifest",
)

ALLOWED_UNTRACKED_PATTERNS = (
    "docs/api/*.md",
    "docs/compliance/*.md",
    "docs/patterns/pilot-profile.md",
    "docs/styles/*.css",
    "docs_theme/*.html",
    "scripts/check_*.py",
    "scripts/release_preflight.py",
    "scripts/install_toolchain.py",
    "scripts/mkdocs_hooks/*.py",
    "src/techrevati/runtime/pilot.py",
    "tests/test_*.py",
)

SNAPSHOT_COUNT_ROWS = (
    ("`docs/api/*.md`", "docs/api/*.md"),
    ("`docs/compliance/*.md`", "docs/compliance/*.md"),
    ("`docs/patterns/pilot-profile.md`", "docs/patterns/pilot-profile.md"),
    ("`docs/styles/*.css`", "docs/styles/*.css"),
    ("`docs_theme/*.html`", "docs_theme/*.html"),
    ("`scripts/check_*.py`", "scripts/check_*.py"),
    ("`scripts/release_preflight.py`", "scripts/release_preflight.py"),
    ("`scripts/install_toolchain.py`", "scripts/install_toolchain.py"),
    ("`scripts/mkdocs_hooks/*.py`", "scripts/mkdocs_hooks/*.py"),
    ("`src/techrevati/runtime/pilot.py`", "src/techrevati/runtime/pilot.py"),
    ("`tests/test_*.py` release guard/test files", "tests/test_*.py"),
)
SNAPSHOT_TOTAL_LABEL = "Total"
SNAPSHOT_ROW_RE = re.compile(r"^\|\s*(?P<label>[^|]+?)\s*\|\s*(?P<count>[0-9,]+)\s*\|")

GENERATED_PARTS = frozenset(
    {
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "dist",
        "htmlcov",
        "site",
    }
)
GENERATED_FILE_NAMES = frozenset({".coverage"})
GENERATED_SUFFIXES = (".pyc", ".pyo")


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
        return [f"mkdocs.yml is missing staging manifest nav: {NAV_SNIPPET}"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if WORKFLOW_SNIPPET not in path.read_text(encoding="utf-8"):
            failures.append(f"{workflow} is missing staging manifest guard")
    return failures


def _check_ci_guardrail_inventory(root: Path) -> list[str]:
    path = root / "scripts" / "check_ci_guardrails.py"
    if not path.is_file():
        return ["scripts/check_ci_guardrails.py is missing"]
    if CI_GUARDRAIL_ENTRY not in path.read_text(encoding="utf-8"):
        return ["check_ci_guardrails.py does not classify check_staging_manifest.py"]
    return []


def _normalise_untracked_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _has_generated_part(path: str) -> bool:
    pure_path = PurePosixPath(path)
    return (
        any(part in GENERATED_PARTS for part in pure_path.parts)
        or pure_path.name in GENERATED_FILE_NAMES
        or path.endswith(GENERATED_SUFFIXES)
    )


def _is_allowed_untracked_path(path: str) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern) for pattern in ALLOWED_UNTRACKED_PATTERNS
    )


def _check_untracked_files(untracked: list[str]) -> list[str]:
    failures: list[str] = []
    paths = sorted(
        _normalise_untracked_path(candidate) for candidate in untracked if candidate
    )
    for path in paths:
        if _has_generated_part(path):
            failures.append(f"generated/local artifact is untracked: {path}")
        elif not _is_allowed_untracked_path(path):
            failures.append(f"untracked file is not classified for staging: {path}")
    return failures


def _documented_snapshot_counts(text: str) -> dict[str, int]:
    labels = {label for label, _pattern in SNAPSHOT_COUNT_ROWS} | {SNAPSHOT_TOTAL_LABEL}
    counts: dict[str, int] = {}
    for line in text.splitlines():
        match = SNAPSHOT_ROW_RE.match(line)
        if match is None:
            continue
        label = match.group("label").strip()
        if label not in labels:
            continue
        counts[label] = int(match.group("count").replace(",", ""))
    return counts


def _current_snapshot_counts(untracked: list[str]) -> dict[str, int]:
    paths = sorted(
        _normalise_untracked_path(candidate) for candidate in untracked if candidate
    )
    counts = {label: 0 for label, _pattern in SNAPSHOT_COUNT_ROWS}
    for path in paths:
        for label, pattern in SNAPSHOT_COUNT_ROWS:
            if fnmatch.fnmatchcase(path, pattern):
                counts[label] += 1
                break
    counts[SNAPSHOT_TOTAL_LABEL] = len(paths)
    return counts


def _check_snapshot_counts(text: str, untracked: list[str]) -> list[str]:
    documented = _documented_snapshot_counts(text)
    current = _current_snapshot_counts(untracked)
    failures: list[str] = []

    for label, current_count in current.items():
        documented_count = documented.get(label)
        if documented_count is None:
            failures.append(f"staging manifest is missing snapshot count row: {label}")
        elif documented_count != current_count:
            failures.append(
                "staging manifest snapshot count drift: "
                f"{label} documents {documented_count}, current is {current_count}"
            )
    return failures


def _git_untracked_files(root: Path) -> tuple[list[str], list[str]]:
    if not (root / ".git").exists():
        return [], []

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
        return [], [f"could not inspect untracked files: {exc}"]
    except subprocess.CalledProcessError as exc:
        details = exc.stderr.strip() or str(exc)
        return [], [f"could not inspect untracked files: {details}"]

    return result.stdout.splitlines(), []


def _check_git_untracked_files(root: Path) -> list[str]:
    if not (root / ".git").exists():
        return []

    untracked, failures = _git_untracked_files(root)
    failures.extend(_check_untracked_files(untracked))
    doc_path = root / DOC
    if doc_path.is_file() and not failures:
        failures.extend(
            _check_snapshot_counts(doc_path.read_text(encoding="utf-8"), untracked)
        )
    return failures


def _failures(root: Path) -> list[str]:
    failures = _check_text_file(
        root,
        DOC,
        groups=(
            ("section", REQUIRED_SECTIONS),
            ("staging manifest control", DOC_REQUIRED_SNIPPETS),
        ),
    )
    failures.extend(
        _check_text_file(
            root,
            FINAL_REVIEW,
            groups=(("staging manifest pointer", FINAL_REVIEW_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            HANDOFF,
            groups=(("staging manifest pointer", HANDOFF_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            INVENTORY,
            groups=(("staging manifest inventory", INVENTORY_REQUIRED_SNIPPETS),),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            PLAN,
            groups=(("staging manifest plan", PLAN_REQUIRED_SNIPPETS),),
        )
    )
    calibration_group = (
        "staging manifest guard calibration",
        GUARD_CALIBRATION_REQUIRED_SNIPPETS,
    )
    failures.extend(
        _check_text_file(root, GUARD_CALIBRATION, groups=(calibration_group,))
    )
    failures.extend(_check_nav(root))
    failures.extend(_check_workflows(root))
    failures.extend(_check_ci_guardrail_inventory(root))
    failures.extend(_check_git_untracked_files(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing staging manifest docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("staging manifest check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Staging manifest check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
