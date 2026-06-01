"""Enforce a per-module coverage floor.

The global ``--cov-fail-under`` in pyproject.toml only catches *project-wide*
regressions. This script catches a single neglected module hiding behind a
healthy aggregate — e.g. ``permissions.py`` slipped to 82% in 0.1.0 while
the total stayed above 90%.

Reads coverage data from the most recent ``.coverage`` file (produced by
``pytest --cov``) and fails with a non-zero exit code if any source module
in ``src/techrevati/`` falls below the threshold. Run AFTER the test suite,
not before — it has nothing to measure on an empty database.

Usage:
    pytest --cov=src/techrevati ...
    python scripts/check_module_coverage.py [--threshold 85]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

DEFAULT_THRESHOLD = 85.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Minimum per-module coverage percent (default: {DEFAULT_THRESHOLD}).",
    )
    args = parser.parse_args()

    # ``coverage json -o -`` writes the report to stdout. We capture and
    # parse instead of touching the filesystem so this works in any CWD
    # that already contains a ``.coverage`` data file.
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "-", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"coverage json failed:\n{result.stderr}", file=sys.stderr)
        return 2

    data = json.loads(result.stdout)
    failures: list[tuple[str, float]] = []
    for path, info in sorted(data.get("files", {}).items()):
        # Skip __init__ stubs and any auto-generated paths.
        summary = info.get("summary", {})
        n_stmts = summary.get("num_statements", 0)
        if n_stmts == 0:
            continue
        pct = float(summary.get("percent_covered", 0.0))
        if pct < args.threshold:
            failures.append((path, pct))

    if failures:
        print(
            f"Per-module coverage floor violated (< {args.threshold}%):",
            file=sys.stderr,
        )
        for path, pct in failures:
            print(f"  {pct:5.1f}%  {path}", file=sys.stderr)
        return 1

    print(f"All modules ≥ {args.threshold}% covered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
