from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_stable_promotion_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_stable_promotion.py"
    )
    scripts_dir = module_path.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("check_stable_promotion", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stable_promotion_doc() -> str:
    module = _load_stable_promotion_module()
    return "\n".join(
        (
            "# Stable Promotion Checklist",
            "Stable promotion status:",
            "branch: `production-rc-0.3.0`,",
            "base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,",
            "current working-tree release diff: 85 files changed, "
            "8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files",
            *module.REQUIRED_SECTIONS,
            *module.DOC_REQUIRED_SNIPPETS,
        )
    )


def _write_fixture(root: Path) -> None:
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance" / "stable-promotion.md").write_text(
        _stable_promotion_doc(),
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Stable Promotion: compliance/stable-promotion.md",
        encoding="utf-8",
    )
    for workflow in ("ci.yml", "release.yml"):
        (root / ".github" / "workflows" / workflow).write_text(
            "python scripts/check_stable_promotion.py",
            encoding="utf-8",
        )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "docs/compliance/stable-promotion.md\n"
        "scripts/check_stable_promotion.py\n"
        "stable promotion record",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "stable promotion record\nStable promotion | Open",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a stable promotion checklist and guard.\n"
        "Stable promotion may start before external evidence is complete | "
        "Mitigated\n"
        "Stable promotion guard passed.",
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        "Stable promotion checklist and guard",
        encoding="utf-8",
    )


def test_stable_promotion_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_stable_promotion_rejects_missing_remote_ci_evidence(
    tmp_path: Path,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "stable-promotion.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "remote CI validation checklist approved",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("remote CI validation checklist" in failure for failure in failures)


def test_stable_promotion_rejects_missing_summary_boundary(
    tmp_path: Path,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "stable promotion record",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("Stable promotion | Open" in failure for failure in failures)


def test_stable_promotion_rejects_missing_workflow_guard(
    tmp_path: Path,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        "jobs: {}",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("release.yml" in failure for failure in failures)


def test_stable_promotion_rejects_missing_inventory_evidence(
    tmp_path: Path,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a stable promotion checklist and guard.",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("external evidence" in failure for failure in failures)


def test_stable_promotion_allows_rc_version_with_pending_record(
    tmp_path: Path,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)

    assert module._check_stable_version_approval(tmp_path, "1.2.3rc1") == []


def test_stable_promotion_blocks_stable_version_without_approval(
    tmp_path: Path,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)

    failures = module._check_stable_version_approval(tmp_path, "1.2.3")

    assert any("Stable promotion status: Approved" in failure for failure in failures)
    assert any("| Decision | Approved |" in failure for failure in failures)


def test_stable_promotion_allows_0x_stable_without_external_evidence(
    tmp_path: Path,
) -> None:
    # Pre-1.0 (0.x) stable versions ship on the automated CI gates; the formal
    # external-evidence promotion only applies from 1.0.0 onward.
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)

    assert module._check_stable_version_approval(tmp_path, "0.4.0") == []


def test_stable_promotion_accepts_stable_version_with_approval(
    tmp_path: Path,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "stable-promotion.md"
    doc.write_text(
        "\n".join(
            (
                doc.read_text(encoding="utf-8"),
                *module.APPROVED_PROMOTION_SNIPPETS,
            )
        ),
        encoding="utf-8",
    )

    assert module._check_stable_version_approval(tmp_path, "1.2.3") == []


def test_stable_promotion_skips_preflight_parity_for_clean_ci_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_stable_promotion_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((0, 0, 0), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (0, []))

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_stable_promotion_accepts_matching_preflight_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_stable_promotion_module()
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


def test_stable_promotion_rejects_branch_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_stable_promotion_module()
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
        "stable promotion branch preflight drift: "
        "documents production-rc-0.3.0, current is main"
    ]


def test_stable_promotion_rejects_release_diff_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_stable_promotion_module()
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
        "stable promotion release diff preflight drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_stable_promotion_rejects_untracked_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_stable_promotion_module()
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
        "stable promotion untracked asset preflight drift: "
        "documents 104, current is 105"
    ]
