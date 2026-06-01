"""Ensure pilot operations guidance stays complete enough for RC use."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RUNBOOK = Path("docs/compliance/pilot-operations-runbook.md")
NAV_SNIPPET = "Pilot Operations Runbook: compliance/pilot-operations-runbook.md"

REQUIRED_SECTIONS = (
    "## Scope",
    "## Required Runtime Wiring",
    "## Minimal OTel Collector",
    "## Signal Map",
    "## Alert Rules",
    "## Retention",
    "## Operator Procedures",
    "## Rollback",
    "## Pilot Shutdown",
    "## Diagnostic Bundle",
    "## Acceptance Evidence",
)

REQUIRED_SIGNALS = (
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

REQUIRED_ALERTS = (
    "Runtime exception spike",
    "Governance terminate spike",
    "Cost threshold breach",
    "Provider failure spike",
    "Sink persistence failure",
    "OTel export failure",
)

REQUIRED_WIRING = (
    "build_pilot_profile",
    "FanoutEventSink",
    "FanoutUsageSink",
    "SqliteEventSink",
    "SqliteUsageSink",
    "OpenTelemetrySink",
    "OpenTelemetryUsageSink",
    "event_sink=",
    "usage_sink=",
    "saver=",
    "thread_id",
    "include_event_detail=True",
)

REQUIRED_PROCEDURES = (
    "Version Check",
    "Event Inspection",
    "Usage Inspection",
    "Governance Breaches",
    "Sink Failure Triage",
    "previous known-good",
    "TECHREVATI_RUNTIME_ROLLBACK_VERSION",
    "Diagnostic Bundle",
    "go/no-go",
)

REQUIRED_QUERIES = (
    "SELECT event, COUNT(*) FROM agent_events",
    "WHERE event IN ('agent.failed', 'governance.breach')",
    "SELECT COUNT(*), ROUND(SUM(cost_usd), 6) FROM usage_records",
    "WHERE event = 'governance.breach'",
)


def _missing_snippets(text: str, snippets: tuple[str, ...]) -> list[str]:
    text_lower = text.lower()
    return [snippet for snippet in snippets if snippet.lower() not in text_lower]


def _check_runbook(root: Path) -> list[str]:
    path = root / RUNBOOK
    if not path.is_file():
        return [f"{RUNBOOK.as_posix()} is missing"]

    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for label, snippets in (
        ("section", REQUIRED_SECTIONS),
        ("signal", REQUIRED_SIGNALS),
        ("alert", REQUIRED_ALERTS),
        ("runtime wiring", REQUIRED_WIRING),
        ("procedure", REQUIRED_PROCEDURES),
        ("operator query", REQUIRED_QUERIES),
    ):
        for snippet in _missing_snippets(text, snippets):
            failures.append(f"operations runbook is missing {label}: {snippet}")
    return failures


def _check_nav(root: Path) -> list[str]:
    mkdocs = root / "mkdocs.yml"
    if not mkdocs.is_file():
        return ["mkdocs.yml is missing"]
    if NAV_SNIPPET not in mkdocs.read_text(encoding="utf-8"):
        return [f"mkdocs.yml is missing operations runbook nav: {NAV_SNIPPET}"]
    return []


def _failures(root: Path) -> list[str]:
    return _check_runbook(root) + _check_nav(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing the operations runbook.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("operations runbook check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Operations runbook check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
