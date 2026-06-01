"""Audit pinned toolchain dependencies for known vulnerabilities."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


AUDITED_GROUPS = ("dev", "docs", "build", "release", "otel")
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _load_pyproject(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _requirements_for_groups(root: Path, groups: Sequence[str]) -> list[str]:
    pyproject = _load_pyproject(root)
    dependencies = list(pyproject["project"].get("dependencies", []))
    extras = pyproject["project"].get("optional-dependencies", {})

    requirements: list[str] = []
    requirements.extend(dependencies)
    for group in groups:
        group_requirements = extras.get(group)
        if group_requirements is None:
            raise ValueError(f"missing optional dependency group: {group}")
        requirements.extend(str(requirement) for requirement in group_requirements)

    return sorted(dict.fromkeys(requirements))


def _write_requirements(path: Path, requirements: Sequence[str]) -> None:
    path.write_text(
        "\n".join((*requirements, "")),
        encoding="utf-8",
    )


def _run_pip_audit(
    requirement_file: Path,
    *,
    runner: Runner | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(requirement_file),
        "--strict",
        "--progress-spinner",
        "off",
    ]
    run = runner or (
        lambda args: subprocess.run(
            args,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    )
    return run(command)


def _audit_dependencies(
    root: Path,
    *,
    groups: Sequence[str] = AUDITED_GROUPS,
    runner: Runner | None = None,
) -> list[str]:
    requirements = _requirements_for_groups(root, groups)
    if not requirements:
        return []

    with tempfile.TemporaryDirectory() as tmp:
        requirement_file = Path(tmp) / "audit-requirements.txt"
        _write_requirements(requirement_file, requirements)
        result = _run_pip_audit(requirement_file, runner=runner)

    if result.returncode == 0:
        return []

    output = (result.stdout or "").strip()
    if "No module named pip_audit" in output:
        return ["pip-audit is not installed; run scripts/install_toolchain.py audit"]
    if not output:
        output = f"pip-audit exited with status {result.returncode}"
    return [f"dependency vulnerability audit failed:\n{output}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pyproject.toml.",
    )
    parser.add_argument(
        "--groups",
        nargs="+",
        default=list(AUDITED_GROUPS),
        help="Optional dependency groups to audit.",
    )
    args = parser.parse_args()

    failures = _audit_dependencies(args.root, groups=tuple(args.groups))
    if failures:
        print("dependency vulnerability check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(
        "Dependency vulnerability check OK: " + ", ".join(args.groups),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
