"""Ensure the release workflow keeps its publish-time safety controls."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REQUIRED_SNIPPETS = (
    "tags:\n      - 'v*'",
    "permissions:\n  contents: read",
    "needs: verify",
    "contents: write",
    "python-version: '3.11'",
    "python scripts/check_release_tag.py",
    "python scripts/check_changelog.py",
    "python scripts/install_toolchain.py release",
    "python -m build",
    "python scripts/check_distribution.py dist",
    "python -m twine check dist/*.whl dist/*.tar.gz",
    "mkdir -p private-package-dist",
    "cp dist/*.whl dist/*.tar.gz private-package-dist/",
    "python scripts/check_distribution.py private-package-dist",
    "python -m twine check private-package-dist/*.whl private-package-dist/*.tar.gz",
    "pip install --force-reinstall --no-index --no-deps --find-links dist "
    "techrevati-runtime",
    "cyclonedx-py environment --output-format json --output-file "
    "dist/sbom.cyclonedx.json",
    "cyclonedx-py environment --output-format xml  --output-file "
    "dist/sbom.cyclonedx.xml",
    "sha256sum *.whl *.tar.gz sbom.cyclonedx.json sbom.cyclonedx.xml > SHA256SUMS",
    "python scripts/check_release_evidence.py dist",
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
    "dist/sbom.cyclonedx.json",
    "dist/sbom.cyclonedx.xml",
    "dist/SHA256SUMS",
    # Public package index via OIDC trusted publishing (no stored token).
    "id-token: write",
    "packages-dir: pypi-dist",
    "Publish to the package index (Trusted Publishing)",
)

FORBIDDEN_SNIPPETS = (
    # Trusted publishing is OIDC-only — stored API tokens stay banned.
    "__token__",
    "api-token:",
    # The public package index uses the default endpoint; an explicit upload or
    # test URL is banned so trusted publishing cannot be silently repointed.
    "repository-url: https://upload." + "pypi.org",
    "repository-url: https://test." + "pypi.org",
    "pip install --no-index --find-links dist techrevati-runtime",
    "pip install --no-index --no-deps --find-links dist techrevati-runtime",
)

ORDERED_STEPS = (
    "python scripts/check_release_tag.py",
    "python scripts/check_changelog.py",
    "python scripts/install_toolchain.py release",
    "python -m build",
    "python scripts/check_distribution.py dist",
    "python -m twine check dist/*.whl dist/*.tar.gz",
    "cp dist/*.whl dist/*.tar.gz private-package-dist/",
    "python scripts/check_distribution.py private-package-dist",
    "python -m twine check private-package-dist/*.whl private-package-dist/*.tar.gz",
    "pip install --force-reinstall --no-index --no-deps --find-links dist "
    "techrevati-runtime",
    "cyclonedx-py environment --output-format json --output-file "
    "dist/sbom.cyclonedx.json",
    "sha256sum *.whl *.tar.gz sbom.cyclonedx.json sbom.cyclonedx.xml > SHA256SUMS",
    "python scripts/check_release_evidence.py dist",
    "Check private package repository configuration",
    "packages-dir: private-package-dist",
)

SECURITY_REQUIRED_SNIPPETS = (
    "## Release Artifact Verification",
    "CycloneDX SBOM",
    "python -m twine check dist/techrevati_runtime-*.whl "
    "dist/techrevati_runtime-*.tar.gz",
    "pip install --force-reinstall --no-index --no-deps --find-links dist "
    "techrevati-runtime",
    "(cd dist && sha256sum -c SHA256SUMS)",
    "dist/sbom.cyclonedx.json",
    "dist/sbom.cyclonedx.xml",
    "dist/SHA256SUMS",
)
SECURITY_FORBIDDEN_LINES = (
    "python -m twine check techrevati_runtime-*.whl techrevati_runtime-*.tar.gz",
    "python -m pip install --force-reinstall --no-index --no-deps "
    "--find-links . techrevati-runtime",
    "sha256sum -c SHA256SUMS",
    "test -s sbom.cyclonedx.json",
    "test -s sbom.cyclonedx.xml",
    "test -s SHA256SUMS",
)


def _check_release_workflow(text: str) -> list[str]:
    failures: list[str] = []
    release_job = text[text.find("  release:") :] if "  release:" in text else text

    for snippet in REQUIRED_SNIPPETS:
        if snippet not in text:
            failures.append(f"release workflow is missing required snippet: {snippet}")

    for snippet in FORBIDDEN_SNIPPETS:
        if snippet in text:
            failures.append(f"release workflow contains forbidden snippet: {snippet}")

    cursor = -1
    for snippet in ORDERED_STEPS:
        index = release_job.find(snippet)
        if index == -1:
            continue
        if index < cursor:
            failures.append(f"release workflow step is out of order: {snippet}")
        cursor = index

    return failures


def _check_security_policy(text: str) -> list[str]:
    failures = [
        f"SECURITY.md is missing release verification snippet: {snippet}"
        for snippet in SECURITY_REQUIRED_SNIPPETS
        if snippet not in text
    ]
    lines = {line.strip() for line in text.splitlines()}
    failures.extend(
        f"SECURITY.md contains root-relative release verification snippet: {line}"
        for line in SECURITY_FORBIDDEN_LINES
        if line in lines
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path(".github/workflows/release.yml"),
        help="Release workflow file to scan.",
    )
    args = parser.parse_args()

    if not args.path.is_file():
        print(f"release workflow check failed: {args.path} is missing", file=sys.stderr)
        return 1

    failures = _check_release_workflow(args.path.read_text(encoding="utf-8"))
    security_path = args.path.parent.parent.parent / "SECURITY.md"
    if not security_path.is_file():
        failures.append("SECURITY.md is missing")
    else:
        failures.extend(
            _check_security_policy(security_path.read_text(encoding="utf-8"))
        )
    if failures:
        print("release workflow check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Release workflow check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
