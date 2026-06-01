from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_rollback_execution_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_rollback_execution.py"
    )
    scripts_dir = module_path.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location(
        "check_rollback_execution",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rollback_execution_doc() -> str:
    return "\n".join(
        (
            "# Rollback Execution Checklist",
            "Rollback execution status: Pending until rollback is proven",
            "## Rollback Preflight Snapshot",
            "Latest rollback preflight snapshot collected",
            "branch: `production-rc-0.3.0`,",
            "base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,",
            "current working-tree release diff: 85 files changed, "
            "8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files",
            "rollback command shape: documented with `--no-index`, `--no-deps`",
            "local pilot dry-run rollback readiness command shape: passed",
            "rollback execution guard: passed",
            "pilot execution guard: passed",
            "previous known-good version",
            "operator sign-off: Pending",
            "preflight evidence only",
            "do not prove rollback",
            "previous known-good runtime package",
            "## Purpose",
            "## Execution Boundary",
            "downstream pilot environment installs the previous known-good version",
            "## Preconditions",
            "previous known-good wheel and source archive",
            "private artifact source",
            "`events.db`, `usage.db`, `checkpoints.db`, and process logs are preserved",
            "without a public package index fallback",
            "## Evidence Preservation",
            "remote CI validation evidence",
            "private RC publication evidence",
            "## Execution Steps",
            "python -m pip install --no-index --no-deps --find-links",
            "TECHREVATI_RUNTIME_ROLLBACK_VERSION",
            "## Verification Commands",
            "importlib.metadata.version('techrevati-runtime')",
            "downstream worker starts",
            "one successful session completes",
            "event sink writes a new event",
            "usage sink writes a new usage record",
            "## Resume And Checkpoint Proof",
            "resume from a checkpoint created before rollback",
            "## Failure Handling",
            "## No-Go Rules",
            "command-shape proof is the only rollback evidence",
            "checkpoint behavior is unknown",
            "## Sign-Off Template",
            "Decision | Pending / Approved / Changes required",
        )
    )


def _write_fixture(root: Path) -> None:
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance" / "rollback-execution.md").write_text(
        _rollback_execution_doc(),
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rollback-proof-checklist.md").write_text(
        "docs/compliance/rollback-execution.md\nreal rollback execution",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "pilot-execution.md").write_text(
        "docs/compliance/rollback-execution.md\nrollback execution",
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Rollback Execution Checklist: compliance/rollback-execution.md",
        encoding="utf-8",
    )
    for workflow in ("ci.yml", "release.yml"):
        (root / ".github" / "workflows" / workflow).write_text(
            "python scripts/check_rollback_execution.py",
            encoding="utf-8",
        )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "docs/compliance/rollback-execution.md\nscripts/check_rollback_execution.py",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "rollback execution checklist\nRollback proof | Open",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a rollback execution checklist and guard.\n"
        "Rollback is not yet proven in pilot environment | Open\n"
        "guarded rollback execution checklist\n"
        "Rollback execution guard passed.",
        encoding="utf-8",
    )


def test_rollback_execution_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_rollback_execution_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_rollback_execution_rejects_command_shape_only(tmp_path: Path) -> None:
    module = _load_rollback_execution_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "rollback-execution.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "command-shape proof is the only rollback evidence",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("command-shape" in failure for failure in failures)


def test_rollback_execution_rejects_missing_proof_pointer(tmp_path: Path) -> None:
    module = _load_rollback_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rollback-proof-checklist.md").write_text(
        "rollback proof",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("rollback-proof-checklist.md" in failure for failure in failures)


def test_rollback_execution_rejects_missing_workflow_guard(tmp_path: Path) -> None:
    module = _load_rollback_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        "",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("release.yml" in failure for failure in failures)


def test_rollback_execution_rejects_missing_inventory_status(tmp_path: Path) -> None:
    module = _load_rollback_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a rollback execution checklist and guard.",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("guarded rollback" in failure for failure in failures)


def test_rollback_execution_skips_preflight_parity_for_clean_ci_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rollback_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((0, 0, 0), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (0, []))

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_rollback_execution_accepts_matching_preflight_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rollback_execution_module()
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


def test_rollback_execution_rejects_branch_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rollback_execution_module()
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
        "rollback execution branch preflight drift: "
        "documents production-rc-0.3.0, current is main"
    ]


def test_rollback_execution_rejects_release_diff_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rollback_execution_module()
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
        "rollback execution release diff preflight drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_rollback_execution_rejects_untracked_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_rollback_execution_module()
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
        "rollback execution untracked asset preflight drift: "
        "documents 104, current is 105"
    ]
