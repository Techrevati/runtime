"""Ensure CI workflows keep the complete guard stack enabled."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERIFY_GUARDS = (
    "check_public_branding.py",
    "check_repo_hygiene.py",
    "check_source_hygiene.py",
    "check_security_patterns.py",
    "check_secret_leaks.py",
    "check_package_policy.py",
    "check_security_review.py",
    "check_public_api.py",
    "check_docs_public_api.py",
    "check_precommit_config.py",
    "check_ci_guardrails.py",
    "check_guard_calibration.py",
    "check_dependency_vulnerabilities.py",
    "check_final_diff_review.py",
    "check_rc_review_handoff.py",
    "check_staging_manifest.py",
    "check_workflow_pinning.py",
    "check_workflow_hardening.py",
    "check_release_workflow.py",
    "check_release_evidence.py",
    "check_rc_readiness.py",
    "check_stable_promotion.py",
    "check_docs_publication.py",
    "check_rollback_execution.py",
    "check_operations_runbook.py",
    "check_pilot_dry_run.py",
    "check_pilot_evidence.py",
    "check_pilot_execution.py",
    "check_private_rc_publication.py",
    "check_maintenance.py",
    "check_python_support.py",
    "check_remote_ci_validation.py",
    "check_toolchain_pins.py",
    "check_version_consistency.py",
    "check_changelog.py",
)
SPECIAL_CHECKS = {
    "check_distribution.py",
    "check_module_coverage.py",
    "check_release_tag.py",
}
CI_BUILD_SNIPPETS = (
    "python scripts/check_distribution.py dist",
    "python -m twine check dist/*.whl dist/*.tar.gz",
    "pip install --no-index --no-deps --find-links dist techrevati-runtime",
)
RELEASE_SNIPPETS = (
    "python scripts/check_release_tag.py",
    "python scripts/check_distribution.py dist",
    "python -m twine check dist/*.whl dist/*.tar.gz",
    "pip install --force-reinstall --no-index --no-deps --find-links dist "
    "techrevati-runtime",
)


def _job_block(text: str, job_name: str) -> str:
    start_marker = f"  {job_name}:"
    start = text.find(start_marker)
    if start == -1:
        return ""
    rest = text[start + len(start_marker) :]
    match = re.search(r"\n  [A-Za-z0-9_-]+:\s*\n", rest)
    if match is None:
        return rest
    return rest[: match.start()]


def _check_guard_inventory(root: Path) -> list[str]:
    known = set(VERIFY_GUARDS) | SPECIAL_CHECKS
    actual = {path.name for path in (root / "scripts").glob("check_*.py")}
    missing_from_inventory = actual - known
    missing_from_scripts = known - actual

    failures: list[str] = []
    for name in sorted(missing_from_inventory):
        failures.append(f"guard script is not classified for CI coverage: {name}")
    for name in sorted(missing_from_scripts):
        failures.append(f"guard inventory references missing script: {name}")
    return failures


def _check_verify_job(workflow_name: str, job_text: str) -> list[str]:
    failures: list[str] = []
    if not job_text:
        return [f"{workflow_name}: verify/test job is missing"]

    for guard in VERIFY_GUARDS:
        command = f"python scripts/{guard}"
        if command not in job_text:
            failures.append(f"{workflow_name}: missing verify guard {command}")

    if "python scripts/check_module_coverage.py --threshold 85" not in job_text:
        failures.append(f"{workflow_name}: missing module coverage guard")
    if "python scripts/install_toolchain.py audit" not in job_text:
        failures.append(f"{workflow_name}: missing audit toolchain install")

    return failures


def _check_required_snippets(
    workflow_name: str,
    workflow_text: str,
    snippets: tuple[str, ...],
) -> list[str]:
    return [
        f"{workflow_name}: missing required snippet {snippet}"
        for snippet in snippets
        if snippet not in workflow_text
    ]


def _check_workflows(root: Path) -> list[str]:
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    failures = _check_verify_job("ci.yml", _job_block(ci, "test"))
    failures.extend(_check_verify_job("release.yml", _job_block(release, "verify")))
    failures.extend(_check_required_snippets("ci.yml", ci, CI_BUILD_SNIPPETS))
    failures.extend(_check_required_snippets("release.yml", release, RELEASE_SNIPPETS))
    return failures


def _failures(root: Path) -> list[str]:
    return _check_guard_inventory(root) + _check_workflows(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing workflow and guard scripts.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("CI guardrail check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("CI guardrail check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
