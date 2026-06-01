"""Ensure local pre-commit hooks match the pinned dev toolchain."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


REQUIRED_HOOK_IDS = {
    "ruff",
    "ruff-format",
    "trailing-whitespace",
    "end-of-file-fixer",
    "check-yaml",
    "check-added-large-files",
    "check-merge-conflict",
    "mypy",
}
PINNED_REV_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[a-z]\d+)?$")
REPO_RE = re.compile(r"^  - repo:\s*(?P<repo>\S+)\s*$")
REV_RE = re.compile(r"^    rev:\s*(?P<rev>\S+)\s*$")
HOOK_RE = re.compile(r"^      - id:\s*(?P<hook>\S+)\s*$")


def _load_pyproject(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _requirement_version(requirements: list[str], name: str) -> str | None:
    prefix = f"{name}=="
    for requirement in requirements:
        if requirement.startswith(prefix):
            return requirement.removeprefix(prefix)
    return None


def _repos(text: str) -> dict[str, dict[str, Any]]:
    repos: dict[str, dict[str, Any]] = {}
    current_repo: str | None = None

    for line in text.splitlines():
        if match := REPO_RE.match(line):
            current_repo = match.group("repo")
            repos[current_repo] = {"hooks": set()}
            continue
        if current_repo is None:
            continue
        if match := REV_RE.match(line):
            repos[current_repo]["rev"] = match.group("rev")
            continue
        if match := HOOK_RE.match(line):
            repos[current_repo]["hooks"].add(match.group("hook"))

    return repos


def _check_precommit(text: str, pyproject: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    repos = _repos(text)
    dev_requirements = pyproject["project"]["optional-dependencies"]["dev"]
    ruff_version = _requirement_version(dev_requirements, "ruff")
    mypy_version = _requirement_version(dev_requirements, "mypy")

    expected_repo_revs = {
        "https://github.com/astral-sh/ruff-pre-commit": f"v{ruff_version}",
        "https://github.com/pre-commit/mirrors-mypy": f"v{mypy_version}",
    }

    for repo, expected_rev in expected_repo_revs.items():
        data = repos.get(repo)
        if data is None:
            failures.append(f"pre-commit config is missing repo: {repo}")
            continue
        if data.get("rev") != expected_rev:
            failures.append(
                f"pre-commit repo {repo} rev {data.get('rev')!r} != {expected_rev!r}"
            )

    for repo, data in repos.items():
        rev = data.get("rev")
        if not isinstance(rev, str) or not PINNED_REV_RE.fullmatch(rev):
            failures.append(f"pre-commit repo {repo} has unpinned rev {rev!r}")

    configured_hooks: set[str] = set()
    for data in repos.values():
        configured_hooks.update(data["hooks"])
    missing_hooks = REQUIRED_HOOK_IDS - configured_hooks
    for hook in sorted(missing_hooks):
        failures.append(f"pre-commit config is missing hook: {hook}")

    if "args: [--fix]" not in text:
        failures.append("ruff pre-commit hook must run with --fix")
    if "args: [--strict]" not in text:
        failures.append("mypy pre-commit hook must run with --strict")

    return failures


def _failures(root: Path) -> list[str]:
    config = root / ".pre-commit-config.yaml"
    if not config.is_file():
        return [".pre-commit-config.yaml is missing"]
    return _check_precommit(
        config.read_text(encoding="utf-8"),
        _load_pyproject(root),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing pre-commit config and pyproject.toml.",
    )
    args = parser.parse_args()

    failures = _failures(args.root)
    if failures:
        print("pre-commit config check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Pre-commit config check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
