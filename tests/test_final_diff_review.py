from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_final_diff_review_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_final_diff_review.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_final_diff_review", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _text(*snippets: str) -> str:
    return "\n\n".join(snippets)


def _write_fixture(tmp_path: Path) -> None:
    module = _load_final_diff_review_module()
    compliance = tmp_path / "docs" / "compliance"
    compliance.mkdir(parents=True)
    (compliance / "final-diff-review.md").write_text(
        _text(
            "# Final Diff Review Checklist",
            *module.REQUIRED_SECTIONS,
            *module.REQUIRED_SUBSYSTEMS,
            *module.REQUIRED_NO_GO_RULES,
            *module.REQUIRED_EVIDENCE,
            *module.REQUIRED_STATUS_SNIPPETS,
            *module.STAGING_REQUIRED_SNIPPETS,
            *module.SNAPSHOT_REQUIRED_SNIPPETS,
            "tracked diff: 85 files changed, 8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files,",
        ),
        encoding="utf-8",
    )
    (compliance / "rc-inventory.md").write_text(
        _text("# Release Candidate Inventory", *module.INVENTORY_REQUIRED_SNIPPETS),
        encoding="utf-8",
    )
    (compliance / "rc-readiness-summary.md").write_text(
        _text("# RC Readiness Summary", *module.SUMMARY_REQUIRED_SNIPPETS),
        encoding="utf-8",
    )
    (tmp_path / "mkdocs.yml").write_text(
        _text(
            "nav:",
            "  - Compliance:",
            "    - Final Diff Review: compliance/final-diff-review.md",
        ),
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for workflow in ("ci.yml", "release.yml"):
        (workflows / workflow).write_text(
            "run: python scripts/check_final_diff_review.py\n",
            encoding="utf-8",
        )


def test_final_diff_review_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_final_diff_review_rejects_missing_subsystem(tmp_path: Path) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)
    review = tmp_path / "docs" / "compliance" / "final-diff-review.md"
    review.write_text(
        review.read_text(encoding="utf-8").replace("Runtime behavior", ""),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("Runtime behavior" in failure for failure in failures)


def test_final_diff_review_rejects_missing_no_go_rule(tmp_path: Path) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)
    review = tmp_path / "docs" / "compliance" / "final-diff-review.md"
    review.write_text(
        review.read_text(encoding="utf-8").replace("secret leak guard fails", ""),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("secret leak guard fails" in failure for failure in failures)


def test_final_diff_review_rejects_missing_workflow_guard(tmp_path: Path) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "jobs: {}\n",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("ci.yml" in failure for failure in failures)


def test_final_diff_review_rejects_missing_nav(tmp_path: Path) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)
    (tmp_path / "mkdocs.yml").write_text("nav:\n", encoding="utf-8")

    failures = module._failures(tmp_path)

    assert any("mkdocs.yml" in failure for failure in failures)


def test_final_diff_review_accepts_matching_snapshot_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(
        module,
        "_git_diff_stats",
        lambda root: ((85, 8859, 1794), []),
    )
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))

    assert module._check_snapshot_parity(tmp_path) == []


def test_final_diff_review_rejects_tracked_snapshot_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(
        module,
        "_git_diff_stats",
        lambda root: ((86, 8859, 1794), []),
    )
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))

    failures = module._check_snapshot_parity(tmp_path)

    assert failures == [
        "final diff review tracked diff snapshot drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_final_diff_review_rejects_untracked_snapshot_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_final_diff_review_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(
        module,
        "_git_diff_stats",
        lambda root: ((85, 8859, 1794), []),
    )
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (105, []))

    failures = module._check_snapshot_parity(tmp_path)

    assert failures == [
        "final diff review untracked asset snapshot drift: "
        "documents 104, current is 105"
    ]
