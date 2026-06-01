from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_pilot_execution_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_pilot_execution.py"
    )
    scripts_dir = module_path.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("check_pilot_execution", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pilot_execution_doc() -> str:
    return "\n".join(
        (
            "# Pilot Execution Checklist",
            "Pilot execution status: Pending until the real controlled pilot "
            "is complete",
            "`scripts/check_pilot_dry_run.py`",
            "## Purpose",
            "## Pilot Preflight Snapshot",
            "Latest pilot preflight snapshot collected",
            "branch: `codex/production-rc-0.3.0`,",
            "base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,",
            "current working-tree release diff: 85 files changed, "
            "8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files",
            "pilot dry-run command: `python scripts/check_pilot_dry_run.py`",
            "pilot dry-run result: passed with 10 local scenarios",
            "pilot execution guard: passed",
            "rollback execution guard: passed",
            "real controlled pilot evidence, downstream telemetry",
            "preflight evidence only",
            "only pilot evidence",
            "## Dry-Run Boundary",
            "Do not use local dry-run output as pilot evidence by itself",
            "does not replace the controlled pilot",
            "## Launch Preconditions",
            "remote CI validation checklist is complete",
            "final reviewer handoff is complete",
            "security review checklist is complete",
            "private RC publication evidence is available",
            "pilot operations runbook has been reviewed",
            "rollback proof checklist has a recorded previous known-good target",
            "## Pilot Window",
            "request-volume cap",
            "## Required Execution Scenarios",
            "successful session",
            "prompt-injection attempt",
            "permission denial",
            "guardrail block",
            "provider failover",
            "checkpoint resume",
            "rollback to previous known-good version",
            "## Required Evidence",
            "durable event records",
            "durable usage records",
            "token usage and estimated cost",
            "## Incident Handling",
            "## Go/No-Go Rules",
            "any P0 incident occurred",
            "any P1 incident remains unresolved",
            "local dry-run output is the only pilot evidence",
            "## Sign-Off Template",
            "Decision | Pending / Approved / Changes required",
        )
    )


def _write_fixture(root: Path) -> None:
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance" / "pilot-execution.md").write_text(
        _pilot_execution_doc(),
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "pilot-evidence-template.md").write_text(
        "docs/compliance/pilot-execution.md\nreal controlled pilot",
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Pilot Execution Checklist: compliance/pilot-execution.md",
        encoding="utf-8",
    )
    for workflow in ("ci.yml", "release.yml"):
        (root / ".github" / "workflows" / workflow).write_text(
            "python scripts/check_pilot_execution.py",
            encoding="utf-8",
        )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "docs/compliance/pilot-execution.md\nscripts/check_pilot_execution.py",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "pilot execution checklist\nControlled RC pilot | Open",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a pilot execution checklist and guard.\n"
        "Local dry-run may be mistaken for real pilot evidence | Mitigated\n"
        "Pilot execution guard passed.",
        encoding="utf-8",
    )


def test_pilot_execution_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_pilot_execution_rejects_missing_dry_run_boundary(tmp_path: Path) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "pilot-execution.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "local dry-run output is the only pilot evidence",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("dry-run" in failure for failure in failures)


def test_pilot_execution_rejects_missing_pilot_evidence_pointer(
    tmp_path: Path,
) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "pilot-evidence-template.md").write_text(
        "pilot evidence",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("pilot-evidence-template.md" in failure for failure in failures)


def test_pilot_execution_rejects_missing_workflow_guard(tmp_path: Path) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("", encoding="utf-8")

    failures = module._failures(tmp_path)
    assert any("ci.yml" in failure for failure in failures)


def test_pilot_execution_rejects_missing_inventory_status(tmp_path: Path) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a pilot execution checklist and guard.",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("Local dry-run" in failure for failure in failures)


def test_pilot_execution_skips_preflight_parity_for_clean_ci_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((0, 0, 0), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (0, []))

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_pilot_execution_accepts_matching_preflight_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((85, 8859, 1794), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))
    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("codex/production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_head",
        lambda root: ("1d57f9c33b6980321d21a20078f2a1ac9a7ed3da", []),
    )

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_pilot_execution_rejects_branch_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_pilot_execution_module()
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
        "pilot execution branch preflight drift: "
        "documents codex/production-rc-0.3.0, current is main"
    ]


def test_pilot_execution_rejects_release_diff_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((86, 8859, 1794), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (104, []))
    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("codex/production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_head",
        lambda root: ("1d57f9c33b6980321d21a20078f2a1ac9a7ed3da", []),
    )

    failures = module._check_preflight_snapshot_parity(tmp_path)

    assert failures == [
        "pilot execution release diff preflight drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_pilot_execution_rejects_untracked_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_pilot_execution_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((85, 8859, 1794), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (105, []))
    monkeypatch.setattr(
        module,
        "_git_branch",
        lambda root: ("codex/production-rc-0.3.0", []),
    )
    monkeypatch.setattr(
        module,
        "_git_head",
        lambda root: ("1d57f9c33b6980321d21a20078f2a1ac9a7ed3da", []),
    )

    failures = module._check_preflight_snapshot_parity(tmp_path)

    assert failures == [
        "pilot execution untracked asset preflight drift: documents 104, current is 105"
    ]
