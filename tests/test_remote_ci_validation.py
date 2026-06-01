from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_remote_ci_validation_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "check_remote_ci_validation.py"
    )
    scripts_dir = module_path.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "check_remote_ci_validation",
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
            "# Remote CI Validation",
            "Remote CI validation status: Pending until remote CI passes",
            "## Purpose",
            "## Preflight Snapshot",
            "branch: `production-rc-0.3.0`,",
            "base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,",
            "current working-tree release diff: 85 files changed, "
            "8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files",
            "This is preflight evidence only",
            "reviewed release-candidate commit",
            "## Commit Parity",
            "reviewed commit SHA",
            "workflow run commit",
            "If the branch changes after remote CI passes",
            "## Required Jobs",
            "test matrix for Python 3.11, 3.12, and 3.13",
            "build matrix for Python 3.11, 3.12, and 3.13",
            "zero-dependency wheel smoke",
            "all verify-time guard scripts",
            "documentation build and public branding checks",
            "## Evidence To Capture",
            "run URL or immutable run identifier",
            "## Failure Triage",
            "product defect",
            "workflow defect",
            "dependency or toolchain outage",
            "## No-Go Rules",
            "remote CI ran on a different commit",
            "## Sign-Off Template",
            "Decision | Pending / Approved / Changes required",
        )
    )


def _ci_workflow_text() -> str:
    return "\n".join(
        (
            "python-version: ['3.11', '3.12', '3.13']",
            "python scripts/check_remote_ci_validation.py",
            "python scripts/check_module_coverage.py --threshold 85",
            "pip install --no-index --no-deps --find-links dist techrevati-runtime",
            "zero-deps-smoke",
        )
    )


def _release_workflow_text() -> str:
    return "\n".join(
        (
            "python-version: ['3.11', '3.12', '3.13']",
            "python scripts/check_remote_ci_validation.py",
            "python scripts/check_release_tag.py",
            "python scripts/check_distribution.py dist",
            "python -m twine check dist/*.whl dist/*.tar.gz",
        )
    )


def _write_fixture(root: Path) -> None:
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance" / "remote-ci-validation.md").write_text(
        _doc_text(),
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Remote CI Validation: compliance/remote-ci-validation.md",
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "ci.yml").write_text(
        _ci_workflow_text(),
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "release.yml").write_text(
        _release_workflow_text(),
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "docs/compliance/remote-ci-validation.md\n"
        "scripts/check_remote_ci_validation.py",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "remote CI validation checklist\nRemote CI validation | Open",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a remote CI validation checklist and guard.\n"
        "Remote CI evidence may mismatch the reviewed commit | Mitigated\n"
        "Remote CI validation guard passed.",
        encoding="utf-8",
    )


def test_remote_ci_validation_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_remote_ci_validation_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_remote_ci_validation_rejects_missing_commit_parity(
    tmp_path: Path,
) -> None:
    module = _load_remote_ci_validation_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "remote-ci-validation.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace("workflow run commit", ""),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("workflow run commit" in failure for failure in failures)


def test_remote_ci_validation_rejects_missing_ci_guard(
    tmp_path: Path,
) -> None:
    module = _load_remote_ci_validation_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "python-version: ['3.11', '3.12', '3.13']",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("ci.yml" in failure for failure in failures)


def test_remote_ci_validation_rejects_missing_release_tag_check(
    tmp_path: Path,
) -> None:
    module = _load_remote_ci_validation_module()
    _write_fixture(tmp_path)
    release = tmp_path / ".github" / "workflows" / "release.yml"
    release.write_text(
        release.read_text(encoding="utf-8").replace(
            "python scripts/check_release_tag.py",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("check_release_tag.py" in failure for failure in failures)


def test_remote_ci_validation_rejects_missing_inventory_evidence(
    tmp_path: Path,
) -> None:
    module = _load_remote_ci_validation_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a remote CI validation checklist and guard.",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("reviewed commit" in failure for failure in failures)


def test_remote_ci_validation_skips_preflight_parity_for_clean_ci_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_remote_ci_validation_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((0, 0, 0), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (0, []))

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_remote_ci_validation_accepts_matching_preflight_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_remote_ci_validation_module()
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


def test_remote_ci_validation_rejects_branch_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_remote_ci_validation_module()
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
        "remote CI validation branch preflight drift: "
        "documents production-rc-0.3.0, current is main"
    ]


def test_remote_ci_validation_rejects_release_diff_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_remote_ci_validation_module()
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
        "remote CI validation release diff preflight drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_remote_ci_validation_rejects_untracked_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_remote_ci_validation_module()
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
        "remote CI validation untracked asset preflight drift: "
        "documents 104, current is 105"
    ]
