"""Ensure release guard calibration stays complete and explicit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DOC = Path("docs/compliance/guard-calibration.md")
INVENTORY = Path("docs/compliance/rc-inventory.md")
NAV_SNIPPET = "Guard Calibration: compliance/guard-calibration.md"
WORKFLOW_SNIPPET = "python scripts/check_guard_calibration.py"
CI_GUARDRAIL_ENTRY = '"check_guard_calibration.py"'

REQUIRED_SECTIONS = (
    "## Purpose",
    "## Guard Inventory",
    "## False-Positive Handling",
    "## CI Parity",
    "## Calibration Procedure",
    "## No-Go Rules",
    "## Sign-Off Template",
)

REQUIRED_SNIPPETS = (
    "Guard calibration status: Pending until remote CI false-positive review is",
    "Do not weaken a guard only to make CI green",
    "bug in guard",
    "bug in repository",
    "intentional policy exception",
    "high-risk controls and must not be bypassed",
    "Every verify-time guard must run in both the CI test job and the release",
    "`scripts/check_ci_guardrails.py` enforces the verify-time and special-case",
    "guard passes locally but fails remote CI without triage",
    "Decision | Pending / Approved / Changes required",
)

INVENTORY_REQUIRED_SNIPPETS = (
    "New guard scripts may be too strict | Mitigated",
    "Guard calibration checklist and false-positive procedure",
)


def _missing_snippets(text: str, snippets: tuple[str, ...]) -> list[str]:
    text_lower = text.lower()
    return [snippet for snippet in snippets if snippet.lower() not in text_lower]


def _guard_scripts(root: Path) -> list[str]:
    return sorted(path.name for path in (root / "scripts").glob("check_*.py"))


def _check_doc(root: Path) -> list[str]:
    path = root / DOC
    if not path.is_file():
        return [f"{DOC.as_posix()} is missing"]

    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for section in _missing_snippets(text, REQUIRED_SECTIONS):
        failures.append(f"{DOC.as_posix()} is missing section: {section}")
    for snippet in _missing_snippets(text, REQUIRED_SNIPPETS):
        failures.append(f"{DOC.as_posix()} is missing required text: {snippet}")
    for guard in _guard_scripts(root):
        if guard not in text:
            failures.append(f"{DOC.as_posix()} is missing guard entry: {guard}")
    return failures


def _check_nav(root: Path) -> list[str]:
    path = root / "mkdocs.yml"
    if not path.is_file():
        return ["mkdocs.yml is missing"]
    if NAV_SNIPPET not in path.read_text(encoding="utf-8"):
        return [f"mkdocs.yml is missing guard calibration nav: {NAV_SNIPPET}"]
    return []


def _check_ci_guardrail_inventory(root: Path) -> list[str]:
    path = root / "scripts" / "check_ci_guardrails.py"
    if not path.is_file():
        return ["scripts/check_ci_guardrails.py is missing"]
    if CI_GUARDRAIL_ENTRY not in path.read_text(encoding="utf-8"):
        return ["check_ci_guardrails.py does not classify check_guard_calibration.py"]
    return []


def _check_workflows(root: Path) -> list[str]:
    failures: list[str] = []
    for workflow in ("ci.yml", "release.yml"):
        path = root / ".github" / "workflows" / workflow
        if not path.is_file():
            failures.append(f"{workflow} is missing")
            continue
        if WORKFLOW_SNIPPET not in path.read_text(encoding="utf-8"):
            failures.append(f"{workflow} is missing guard calibration guard")
    return failures


def _check_inventory(root: Path) -> list[str]:
    path = root / INVENTORY
    if not path.is_file():
        return [f"{INVENTORY.as_posix()} is missing"]
    text = path.read_text(encoding="utf-8")
    return [
        f"{INVENTORY.as_posix()} is missing guard calibration text: {snippet}"
        for snippet in _missing_snippets(text, INVENTORY_REQUIRED_SNIPPETS)
    ]


def _failures(root: Path) -> list[str]:
    failures = _check_doc(root)
    failures.extend(_check_nav(root))
    failures.extend(_check_ci_guardrail_inventory(root))
    failures.extend(_check_workflows(root))
    failures.extend(_check_inventory(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing guard calibration docs and workflows.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("guard calibration check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Guard calibration check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
