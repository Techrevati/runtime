"""Ensure controlled pilot and rollback evidence templates stay complete."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PILOT_TEMPLATE = Path("docs/compliance/pilot-evidence-template.md")
ROLLBACK_TEMPLATE = Path("docs/compliance/rollback-proof-checklist.md")
PILOT_NAV = "Controlled RC Pilot Evidence: compliance/pilot-evidence-template.md"
ROLLBACK_NAV = "Rollback Proof Checklist: compliance/rollback-proof-checklist.md"

PILOT_REQUIRED_SECTIONS = (
    "## Pilot Identification",
    "## Preconditions",
    "## Runtime Configuration",
    "## Controlled Scenarios",
    "## Signal Evidence",
    "## Incident Review",
    "## Success Criteria",
    "## Rollback Evidence",
    "## Diagnostic Bundle",
    "## Go/No-Go Decision",
)

PILOT_REQUIRED_SCENARIOS = (
    "Successful session",
    "Prompt-injection attempt",
    "Permission denial",
    "Guardrail block",
    "Max-iterations breach",
    "Max-tool-calls breach",
    "Provider failover",
    "Checkpoint resume",
    "Sink failure diagnostic",
    "Rollback to previous known-good version",
)

PILOT_REQUIRED_SIGNALS = (
    "Session count",
    "Success rate",
    "Failure rate",
    "Guardrail blocks",
    "Permission denials",
    "Governance breaches",
    "Retry attempts",
    "Provider switches",
    "Token usage",
    "Estimated cost",
    "Tool call count",
    "Checkpoint writes",
    "Checkpoint replays",
    "Event sink failures",
    "Usage sink failures",
    "Turn latency",
    "Tool-call latency",
)

PILOT_REQUIRED_CRITERIA = (
    "Zero P0 incidents",
    "Zero unresolved P1 incidents",
    "At least 99 percent of sessions complete without runtime crash",
    "Rollback has been tested and documented",
    "Decision: `go` / `no-go`",
)

PILOT_REQUIRED_PRECONDITIONS = (
    "Local/server production gate is green",
    "Remote CI is green on the pilot commit",
    "Public branding guard passes",
    "Secret leak guard passes",
    "Dependency vulnerability guard passes",
    "Rollback target and rollback command are recorded",
)

ROLLBACK_REQUIRED_SECTIONS = (
    "## Rollback Scope",
    "## Preconditions",
    "## Evidence Preservation",
    "## Rollback Command",
    "## Version Proof",
    "## Smoke Test",
    "## Resume And Checkpoint Proof",
    "## Acceptance Criteria",
    "## Rollback Decision",
)

ROLLBACK_REQUIRED_SNIPPETS = (
    "Previous known-good version",
    "controlled artifact channel",
    "events.db",
    "usage.db",
    "checkpoints.db",
    "TECHREVATI_RUNTIME_ROLLBACK_VERSION",
    "--no-index --no-deps",
    "importlib.metadata.version",
    "Downstream worker starts",
    "One successful session completes",
    "Event sink writes a new event",
    "Usage sink writes a new usage record",
    "Checkpoint saver can write",
    "Rollback proven: `yes` / `no`",
)


def _missing_snippets(text: str, snippets: tuple[str, ...]) -> list[str]:
    text_lower = text.lower()
    return [snippet for snippet in snippets if snippet.lower() not in text_lower]


def _check_file(
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
    text = mkdocs.read_text(encoding="utf-8")
    failures: list[str] = []
    for snippet in (PILOT_NAV, ROLLBACK_NAV):
        if snippet not in text:
            failures.append(f"mkdocs.yml is missing pilot evidence nav: {snippet}")
    return failures


def _failures(root: Path) -> list[str]:
    failures = _check_file(
        root,
        PILOT_TEMPLATE,
        groups=(
            ("section", PILOT_REQUIRED_SECTIONS),
            ("scenario", PILOT_REQUIRED_SCENARIOS),
            ("signal", PILOT_REQUIRED_SIGNALS),
            ("success criterion", PILOT_REQUIRED_CRITERIA),
            ("precondition", PILOT_REQUIRED_PRECONDITIONS),
        ),
    )
    failures.extend(
        _check_file(
            root,
            ROLLBACK_TEMPLATE,
            groups=(
                ("section", ROLLBACK_REQUIRED_SECTIONS),
                ("rollback proof", ROLLBACK_REQUIRED_SNIPPETS),
            ),
        )
    )
    failures.extend(_check_nav(root))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pilot evidence templates.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("pilot evidence check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Pilot evidence check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
