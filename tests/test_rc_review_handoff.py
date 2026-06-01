from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_rc_review_handoff_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_rc_review_handoff.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_rc_review_handoff",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _handoff_doc() -> str:
    return "\n".join(
        (
            "# RC Review Handoff",
            "RC review handoff status: Pending until final reviewer signs off",
            "## Purpose",
            "## Package Snapshot",
            "`techrevati-runtime`",
            "`techrevati.runtime`",
            "`0.3.0rc1`",
            "controlled internal",
            "public package index publication: out of scope until controlled pilot",
            "## Review Inputs",
            "docs/compliance/rc-inventory.md",
            "docs/compliance/final-diff-review.md",
            "docs/compliance/staging-manifest.md",
            "docs/compliance/remote-ci-validation.md",
            "docs/compliance/security-review.md",
            "docs/compliance/private-rc-publication.md",
            "docs/compliance/pilot-evidence-template.md",
            "docs/compliance/rollback-proof-checklist.md",
            "git status --short --branch",
            "git ls-files --others --exclude-standard",
            "git diff --stat",
            "## Diff Review Checklist",
            "untracked release assets are not classified",
            "public branding limited to Techrevati doo",
            "## Gate Evidence Summary",
            "all verify-time guard results",
            "staging manifest guard",
            "Current handoff snapshot collected",
            "branch: `production-rc-0.3.0`,",
            "tracked diff: 85 files changed, 8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files",
            "## External Blockers",
            "Remote CI validation",
            "Controlled RC pilot",
            "Rollback proof",
            "## No-Go Rules",
            "Do not stage, tag, publish, pilot, or promote",
            "reviewer handoff is unsigned",
            "`techrevati.runtime` namespace changes",
            "## Sign-Off Template",
            "Decision | Pending / Approved / Changes required",
        )
    )


def _write_fixture(root: Path) -> None:
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance" / "rc-review-handoff.md").write_text(
        _handoff_doc(),
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "final-diff-review.md").write_text(
        "docs/compliance/rc-review-handoff.md\n"
        "docs/compliance/staging-manifest.md\n"
        "reviewer handoff",
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "RC Review Handoff: compliance/rc-review-handoff.md",
        encoding="utf-8",
    )
    for workflow in ("ci.yml", "release.yml"):
        (root / ".github" / "workflows" / workflow).write_text(
            "python scripts/check_rc_review_handoff.py",
            encoding="utf-8",
        )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "docs/compliance/rc-review-handoff.md\n"
        "docs/compliance/staging-manifest.md\n"
        "scripts/check_rc_review_handoff.py\n"
        "scripts/check_staging_manifest.py",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "reviewer handoff\nFinal diff review | Open",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add an RC reviewer handoff checklist and guard.\n"
        "Add a staging manifest checklist and guard.\n"
        "Final reviewer handoff may miss blockers | Mitigated\n"
        "Untracked RC assets may be missed during staging | Mitigated\n"
        "RC reviewer handoff guard passed.\n"
        "Staging manifest guard passed.",
        encoding="utf-8",
    )


def test_rc_review_handoff_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_rc_review_handoff_rejects_missing_no_go_rule(tmp_path: Path) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "rc-review-handoff.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "reviewer handoff is unsigned",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("reviewer handoff is unsigned" in failure for failure in failures)


def test_rc_review_handoff_rejects_missing_final_review_pointer(
    tmp_path: Path,
) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "final-diff-review.md").write_text(
        "Final diff review",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("final-diff-review.md" in failure for failure in failures)


def test_rc_review_handoff_rejects_missing_workflow_guard(tmp_path: Path) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        "",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("release.yml" in failure for failure in failures)


def test_rc_review_handoff_rejects_missing_inventory_status(tmp_path: Path) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add an RC reviewer handoff checklist and guard.",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("Final reviewer handoff" in failure for failure in failures)


def test_rc_review_handoff_accepts_matching_snapshot_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_diff_stats",
        lambda root: ((85, 8859, 1794), []),
    )
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))

    assert module._check_snapshot_parity(tmp_path) == []


def test_rc_review_handoff_rejects_branch_snapshot_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_branch", lambda root: ("main", []))
    monkeypatch.setattr(
        module,
        "_git_diff_stats",
        lambda root: ((85, 8859, 1794), []),
    )
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))

    failures = module._check_snapshot_parity(tmp_path)

    assert failures == [
        "RC review handoff branch snapshot drift: "
        "documents production-rc-0.3.0, current is main"
    ]


def test_rc_review_handoff_rejects_tracked_snapshot_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_diff_stats",
        lambda root: ((86, 8859, 1794), []),
    )
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))

    failures = module._check_snapshot_parity(tmp_path)

    assert failures == [
        "RC review handoff tracked diff snapshot drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_rc_review_handoff_rejects_untracked_snapshot_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rc_review_handoff_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_diff_stats",
        lambda root: ((85, 8859, 1794), []),
    )
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (105, []))

    failures = module._check_snapshot_parity(tmp_path)

    assert failures == [
        "RC review handoff untracked asset snapshot drift: "
        "documents 104, current is 105"
    ]
