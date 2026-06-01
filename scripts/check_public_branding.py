"""Block public branding regressions.

The package can keep its technical namespace and integration API names, but
public-facing copy must stay vendor-neutral except for Techrevati doo.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

DEFAULT_PATHS = (
    "README.md",
    "CHANGELOG.md",
    "CODEOWNERS",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "mkdocs.yml",
    "pyproject.toml",
    "docs",
    "docs_theme",
    "scripts",
    "src",
    "tests",
    "examples",
    ".github",
)


def _term(*parts: str) -> str:
    return "".join(parts)


BANNED_PUBLIC_TERMS = (
    _term("Anth", "ropic"),
    _term("Ar", "min"),
    _term("Code", "cov"),
    _term("Documentation ", "built with"),
    _term("Boot", "strap"),
    _term("Font ", "Awesome"),
    _term("Git", "Hub"),
    _term("Lang", "Graph"),
    _term("Made with ", "Material"),
    _term("Material ", "for ", "MkDocs"),
    _term("Mat", "effy"),
    _term("mkdocs", "-", "material"),
    _term("Open", "AI"),
    _term("Pyd", "antic"),
    _term("Py", "PI"),
    _term("squid", "funk"),
    _term("Temp", "oral"),
)

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}

SKIP_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".png",
    ".pyc",
    ".pyo",
    ".svg",
    ".webp",
}


def _iter_files(paths: Sequence[Path], *, html_only: bool = False) -> Iterable[Path]:
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() not in SKIP_SUFFIXES:
                if not html_only or root.suffix.lower() == ".html":
                    yield root
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            if html_only and path.suffix.lower() != ".html":
                continue
            yield path


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for term in BANNED_PUBLIC_TERMS:
            if term in line:
                findings.append((line_number, term, line.strip()))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(path) for path in DEFAULT_PATHS],
        help="Files or directories to scan.",
    )
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Only scan generated HTML files under the selected paths.",
    )
    args = parser.parse_args()

    failed = False
    for path in sorted(set(_iter_files(args.paths, html_only=args.html_only))):
        findings = _scan_file(path)
        for line_number, term, line in findings:
            if not failed:
                print("public branding check failed:", file=sys.stderr)
            failed = True
            print(
                f"  {path}:{line_number}: blocked term {term!r}: {line}",
                file=sys.stderr,
            )

    if failed:
        return 1

    print("Public branding check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
