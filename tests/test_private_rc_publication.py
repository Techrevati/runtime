from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_private_rc_publication_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "check_private_rc_publication.py"
    )
    scripts_dir = module_path.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "check_private_rc_publication",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc_text() -> str:
    return "\n".join(
        (
            "# Private RC Publication",
            "Private RC publication status: Pending until `0.3.0rc1` is published",
            "## Purpose",
            "## Publication Preflight Snapshot",
            "Latest private publication preflight snapshot collected",
            "branch: `production-rc-0.3.0`,",
            "base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,",
            "current working-tree release diff: 85 files changed, "
            "8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files",
            "fresh wheel and source archive build for `0.3.0rc1`: passed",
            "package metadata rendering with explicit wheel and source archive",
            "local SBOM JSON, SBOM XML, and `SHA256SUMS` generation: passed",
            "release evidence guard against local `dist`: passed",
            (
                "temporary private-package upload staging with wheel and source "
                "archive only"
            ),
            "private-package staged artifact distribution and metadata checks: passed",
            "private registry secret validation",
            "preflight evidence only",
            "## Publication Boundary",
            "Public package index publication is out of scope until pilot approval",
            "## Required Inputs",
            "private repository URL, username, and password/token",
            "## Artifact Verification",
            "`techrevati_runtime-0.3.0rc1-py3-none-any.whl`",
            "`techrevati_runtime-0.3.0rc1.tar.gz`",
            "`sbom.cyclonedx.json`",
            "`sbom.cyclonedx.xml`",
            "`SHA256SUMS`",
            "python scripts/check_distribution.py dist",
            "python scripts/check_release_evidence.py dist",
            "python -m twine check dist/*.whl dist/*.tar.gz",
            "pip install --force-reinstall --no-index --no-deps --find-links dist "
            "techrevati-runtime",
            "(cd dist && sha256sum -c SHA256SUMS)",
            "approved security review with no unresolved high or critical findings",
            "must contain only wheel and source archive files",
            "SBOM files and `SHA256SUMS` stay attached",
            "## Private Channel Controls",
            "workflow falls back to a default public package index",
            "## Credential Handling",
            "must never be committed, printed in logs, stored in release",
            "## Publication Procedure",
            "## No-Go Rules",
            "security review is unsigned",
            "rollback target is not known",
            "## Evidence Template",
            "Decision | Pending / Approved / Changes required",
        )
    )


def _release_workflow_text() -> str:
    return "\n".join(
        (
            "jobs:",
            "  verify:",
            "    steps:",
            "      - run: python scripts/check_private_rc_publication.py",
            "  release:",
            "    steps:",
            "      - name: Stage package artifacts",
            "        run: |",
            "          mkdir -p private-package-dist",
            "          cp dist/*.whl dist/*.tar.gz private-package-dist/",
            "          python scripts/check_distribution.py private-package-dist",
            (
                "          python -m twine check private-package-dist/*.whl "
                "private-package-dist/*.tar.gz"
            ),
            "      - name: Generate artifact checksums",
            "        run: |",
            "          pip install --force-reinstall --no-index --no-deps "
            "--find-links dist techrevati-runtime",
            "          cd dist",
            "          sha256sum *.whl *.tar.gz sbom.cyclonedx.json "
            "sbom.cyclonedx.xml > SHA256SUMS",
            "      - name: Verify release evidence",
            "        run: python scripts/check_release_evidence.py dist",
            "      - name: Create release",
            "        with:",
            "          files: |",
            "            dist/SHA256SUMS",
            "      - name: Check private package repository configuration",
            "        env:",
            "          PRIVATE_PACKAGE_REPOSITORY_URL: "
            "${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}",
            "          PRIVATE_PACKAGE_USERNAME: "
            "${{ secrets.PRIVATE_PACKAGE_USERNAME }}",
            "          PRIVATE_PACKAGE_PASSWORD: "
            "${{ secrets.PRIVATE_PACKAGE_PASSWORD }}",
            "        run: |",
            '          test -n "$PRIVATE_PACKAGE_REPOSITORY_URL"',
            '          test -n "$PRIVATE_PACKAGE_USERNAME"',
            '          test -n "$PRIVATE_PACKAGE_PASSWORD"',
            "      - name: Publish private package",
            "        with:",
            "          packages-dir: private-package-dist",
            "          repository-url: ${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}",
            "          user: ${{ secrets.PRIVATE_PACKAGE_USERNAME }}",
            "          password: ${{ secrets.PRIVATE_PACKAGE_PASSWORD }}",
        )
    )


def _write_fixture(root: Path) -> None:
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance" / "private-rc-publication.md").write_text(
        _doc_text(),
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Private RC Publication: compliance/private-rc-publication.md",
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "python scripts/check_private_rc_publication.py",
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "release.yml").write_text(
        _release_workflow_text(),
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "docs/compliance/private-rc-publication.md\n"
        "scripts/check_private_rc_publication.py",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "private RC publication checklist\nprivate package repository URL",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a private RC publication checklist and guard.\n"
        "Private RC publication may publish wrong artifacts or channel | Mitigated",
        encoding="utf-8",
    )


def test_private_rc_publication_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_private_rc_publication_rejects_missing_doc_policy(
    tmp_path: Path,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "private-rc-publication.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "Public package index publication is out of scope until pilot approval",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("public package index" in failure.lower() for failure in failures)


def test_private_rc_publication_rejects_missing_private_repository_url(
    tmp_path: Path,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "release.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "          repository-url: ${{ secrets.PRIVATE_PACKAGE_REPOSITORY_URL }}\n",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("repository-url" in failure for failure in failures)


def test_private_rc_publication_rejects_missing_staged_artifact_check(
    tmp_path: Path,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "release.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "          python scripts/check_distribution.py private-package-dist\n",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("private-package-dist" in failure for failure in failures)


def test_private_rc_publication_rejects_public_package_fallback(
    tmp_path: Path,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "release.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8")
        + "\n          repository-url: https://upload."
        + "pypi.org/legacy/\n",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("forbidden" in failure for failure in failures)


def test_private_rc_publication_rejects_missing_nav(tmp_path: Path) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    (tmp_path / "mkdocs.yml").write_text("", encoding="utf-8")

    failures = module._failures(tmp_path)
    assert any("mkdocs.yml" in failure for failure in failures)


def test_private_rc_publication_rejects_root_relative_checksum_command(
    tmp_path: Path,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "private-rc-publication.md"
    doc.write_text(
        doc.read_text(encoding="utf-8") + "\n```bash\nsha256sum -c SHA256SUMS\n```\n",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("root-relative publication command" in failure for failure in failures)


def test_private_rc_publication_rejects_broad_dist_twine_check(
    tmp_path: Path,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "private-rc-publication.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "python -m twine check dist/*.whl dist/*.tar.gz",
            "python -m twine check dist/*",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("dist/*.whl dist/*.tar.gz" in failure for failure in failures)


def test_private_rc_publication_skips_preflight_parity_for_clean_ci_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((0, 0, 0), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (0, []))

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_private_rc_publication_accepts_matching_preflight_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((85, 8859, 1794), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))
    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_head",
        lambda root: ("1d57f9c33b6980321d21a20078f2a1ac9a7ed3da", []),
    )

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_private_rc_publication_rejects_branch_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((85, 8859, 1794), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))
    monkeypatch.setattr(module, "_git_branch", lambda root: ("main", []))
    monkeypatch.setattr(
        module,
        "_git_head",
        lambda root: ("1d57f9c33b6980321d21a20078f2a1ac9a7ed3da", []),
    )

    failures = module._check_preflight_snapshot_parity(tmp_path)

    assert failures == [
        "private RC publication branch preflight drift: "
        "documents production-rc-0.3.0, current is main"
    ]


def test_private_rc_publication_rejects_release_diff_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((86, 8859, 1794), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))
    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_head",
        lambda root: ("1d57f9c33b6980321d21a20078f2a1ac9a7ed3da", []),
    )

    failures = module._check_preflight_snapshot_parity(tmp_path)

    assert failures == [
        "private RC publication release diff preflight drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_private_rc_publication_rejects_untracked_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_private_rc_publication_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((85, 8859, 1794), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (105, []))
    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_head",
        lambda root: ("1d57f9c33b6980321d21a20078f2a1ac9a7ed3da", []),
    )

    failures = module._check_preflight_snapshot_parity(tmp_path)

    assert failures == [
        "private RC publication untracked asset preflight drift: "
        "documents 104, current is 105"
    ]
