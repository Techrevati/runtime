from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_release_workflow_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_release_workflow.py"
    )
    spec = importlib.util.spec_from_file_location("check_release_workflow", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


JSON_SBOM_COMMAND = (
    "cyclonedx-py environment --output-format json --output-file "
    "dist/sbom.cyclonedx.json"
)
XML_SBOM_COMMAND = (
    "cyclonedx-py environment --output-format xml  --output-file "
    "dist/sbom.cyclonedx.xml"
)
DIST_INSTALL_COMMAND = (
    "pip install --force-reinstall --no-index --no-deps --find-links dist "
    "techrevati-runtime"
)
SECURITY_INSTALL_COMMAND = (
    "python -m pip install --force-reinstall --no-index --no-deps "
    "--find-links dist techrevati-runtime"
)
PUBLISH_STEP = (
    "      - name: Publish private package\n"
    "        with:\n"
    "          packages-dir: private-package-dist\n"
    "          repository-url: ${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}\n"
    "          user: ${{ secrets.PRIVATE_PACKAGE_USERNAME }}\n"
    "          password: ${{ secrets.PRIVATE_PACKAGE_PASSWORD }}\n"
)


VALID_WORKFLOW = f"""
on:
  push:
    tags:
      - 'v*'

permissions:
  contents: read

jobs:
    release:
    needs: verify
    permissions:
      contents: write
      id-token: write
    steps:
      - name: Set up Python
        with:
          python-version: '3.11'
      - name: Check release tag
        run: python scripts/check_release_tag.py
      - name: Changelog version guard
        run: python scripts/check_changelog.py
      - name: Install build tools
        run: python scripts/install_toolchain.py release
      - name: Build distribution
        run: python -m build
      - name: Check distribution artifacts
        run: python scripts/check_distribution.py dist
      - name: Check package metadata rendering
        run: python -m twine check dist/*.whl dist/*.tar.gz
      - name: Stage package artifacts
        run: |
          mkdir -p private-package-dist
          cp dist/*.whl dist/*.tar.gz private-package-dist/
      - name: Check staged private package artifacts
        run: |
          python scripts/check_distribution.py private-package-dist
          python -m twine check private-package-dist/*.whl private-package-dist/*.tar.gz
      - name: Generate CycloneDX SBOM
        run: |
          {DIST_INSTALL_COMMAND}
          {JSON_SBOM_COMMAND}
          {XML_SBOM_COMMAND}
      - name: Generate artifact checksums
        run: |
          cd dist
          sha256sum *.whl *.tar.gz sbom.cyclonedx.json sbom.cyclonedx.xml > SHA256SUMS
      - name: Verify release evidence
        run: python scripts/check_release_evidence.py dist
      - name: Check private package repository configuration
        env:
          PRIVATE_PACKAGE_REPOSITORY_URL: __PRIVATE_REPOSITORY_URL__
          PRIVATE_PACKAGE_USERNAME: ${{{{ secrets.PRIVATE_PACKAGE_USERNAME }}}}
          PRIVATE_PACKAGE_PASSWORD: ${{{{ secrets.PRIVATE_PACKAGE_PASSWORD }}}}
        run: |
          test -n "$PRIVATE_PACKAGE_REPOSITORY_URL"
          test -n "$PRIVATE_PACKAGE_USERNAME"
          test -n "$PRIVATE_PACKAGE_PASSWORD"
      - name: Publish private package
        with:
          packages-dir: private-package-dist
          repository-url: __PRIVATE_REPOSITORY_URL__
          user: ${{{{ secrets.PRIVATE_PACKAGE_USERNAME }}}}
          password: ${{{{ secrets.PRIVATE_PACKAGE_PASSWORD }}}}
      - name: Publish to the package index (Trusted Publishing)
        with:
          packages-dir: pypi-dist
      - name: Create release
        with:
          files: |
            dist/sbom.cyclonedx.json
            dist/sbom.cyclonedx.xml
            dist/SHA256SUMS
""".replace(
    "__PRIVATE_REPOSITORY_URL__",
    "${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}",
)

VALID_SECURITY = f"""
## Release Artifact Verification

Every release should publish exactly the package artifacts plus CycloneDX SBOM
files.

```bash
{SECURITY_INSTALL_COMMAND}
(cd dist && sha256sum -c SHA256SUMS)
python -m twine check dist/techrevati_runtime-*.whl dist/techrevati_runtime-*.tar.gz
test -s dist/sbom.cyclonedx.json
test -s dist/sbom.cyclonedx.xml
test -s dist/SHA256SUMS
```
"""


def test_release_workflow_accepts_expected_controls() -> None:
    module = _load_release_workflow_module()
    assert module._check_release_workflow(VALID_WORKFLOW) == []
    assert module._check_security_policy(VALID_SECURITY) == []


def test_release_workflow_rejects_missing_required_snippet() -> None:
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW.replace(
        "          repository-url: ${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}\n",
        "",
    )
    failures = module._check_release_workflow(workflow)
    assert any("repository-url" in failure for failure in failures)


def test_release_workflow_rejects_token_publish_config() -> None:
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW + "\n          password: __token__\n"
    failures = module._check_release_workflow(workflow)
    assert any("forbidden" in failure for failure in failures)


def test_release_workflow_requires_id_token_for_trusted_publishing() -> None:
    # id-token: write is now REQUIRED for OIDC trusted publishing; removing it
    # must fail (the inverse of the old policy, which forbade it).
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW.replace("      id-token: write\n", "")
    failures = module._check_release_workflow(workflow)
    assert any("id-token" in failure for failure in failures)


def test_release_workflow_rejects_dependency_resolving_local_install() -> None:
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW.replace(
        DIST_INSTALL_COMMAND,
        "pip install --no-index --find-links dist techrevati-runtime",
    )

    failures = module._check_release_workflow(workflow)
    assert any("no-deps" in failure or "forbidden" in failure for failure in failures)


def test_release_workflow_rejects_non_forced_local_install() -> None:
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW.replace(
        DIST_INSTALL_COMMAND,
        "pip install --no-index --no-deps --find-links dist techrevati-runtime",
    )

    failures = module._check_release_workflow(workflow)

    assert any(
        "force-reinstall" in failure or "forbidden" in failure for failure in failures
    )


def test_release_workflow_rejects_out_of_order_publish() -> None:
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW.replace(PUBLISH_STEP, "")
    workflow = workflow.replace(
        "      - name: Generate CycloneDX SBOM",
        PUBLISH_STEP + "      - name: Generate CycloneDX SBOM",
    )
    failures = module._check_release_workflow(workflow)
    assert any("out of order" in failure for failure in failures)


def test_release_workflow_rejects_missing_checksum_manifest() -> None:
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW.replace(
        "          sha256sum *.whl *.tar.gz sbom.cyclonedx.json "
        "sbom.cyclonedx.xml > SHA256SUMS\n",
        "",
    )

    failures = module._check_release_workflow(workflow)

    assert any("SHA256SUMS" in failure for failure in failures)


def test_release_workflow_rejects_missing_private_package_dist_check() -> None:
    module = _load_release_workflow_module()
    workflow = VALID_WORKFLOW.replace(
        "          python scripts/check_distribution.py private-package-dist\n",
        "",
    )

    failures = module._check_release_workflow(workflow)

    assert any("private-package-dist" in failure for failure in failures)


def test_release_workflow_rejects_missing_security_verification() -> None:
    module = _load_release_workflow_module()

    failures = module._check_security_policy("## Security Policy\n")

    assert any("SECURITY.md" in failure for failure in failures)
    assert any("twine check" in failure for failure in failures)


def test_release_workflow_rejects_root_relative_security_commands() -> None:
    module = _load_release_workflow_module()
    old_security = "\n".join(
        (
            "## Release Artifact Verification",
            "",
            "CycloneDX SBOM",
            "",
            "python -m twine check techrevati_runtime-*.whl "
            "techrevati_runtime-*.tar.gz",
            "python -m pip install --force-reinstall --no-index --no-deps "
            "--find-links . techrevati-runtime",
            "sha256sum -c SHA256SUMS",
            "test -s sbom.cyclonedx.json",
            "test -s sbom.cyclonedx.xml",
            "test -s SHA256SUMS",
        )
    )

    failures = module._check_security_policy(old_security)

    assert any("dist/techrevati_runtime" in failure for failure in failures)
    assert any("find-links dist" in failure for failure in failures)
    assert any(
        "(cd dist && sha256sum -c SHA256SUMS)" in failure for failure in failures
    )
