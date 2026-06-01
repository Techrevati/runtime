from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_security_review_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_security_review.py"
    )
    scripts_dir = module_path.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("check_security_review", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _security_review_doc() -> str:
    return "\n".join(
        (
            "# Security Review",
            "Security review status: Pending until reviewer signs off",
            "## Purpose",
            "library, not a service",
            "in-process dependency",
            "## Security Preflight Snapshot",
            "Latest security preflight snapshot collected",
            "branch: `production-rc-0.3.0`,",
            "base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,",
            "current working-tree release diff: 85 files changed, "
            "8,859 insertions, 1,794 deletions,",
            "untracked release assets: 104 files",
            "secret leak guard: passed",
            "dependency vulnerability guard: passed",
            "security pattern guard: passed",
            "workflow action pinning guard: passed",
            "workflow hardening guard: passed",
            "release workflow guard: passed",
            "private RC publication guard: passed",
            "public branding guard: passed",
            "package metadata rendering with `twine check`: passed",
            "local SBOM JSON, SBOM XML, and `SHA256SUMS` generation: passed",
            "release evidence guard against local `dist`: passed",
            "remote CI and security reviewer sign-off: Pending",
            "preflight evidence only",
            "## Review Scope",
            "## Required Evidence",
            "secret leak guard output",
            "dependency vulnerability guard output",
            "security pattern guard output",
            "workflow action pinning guard output",
            "workflow hardening guard output",
            "private RC publication guard output",
            "public branding guard output",
            "## Runtime Risk Review",
            "model output is untrusted",
            "tool implementations run with caller process privileges",
            "`PermissionEnforcer` is a policy gate, not a sandbox",
            "guardrails reduce risk but do not isolate tool bodies",
            "## Supply Chain Review",
            "runtime required dependency set remains empty",
            "critical findings",
            "## Secret And Data Exposure Review",
            "built-in model I/O logging is metadata-only by default",
            "OTel event detail export is opt-in",
            "caller-driven cancellation classifies as cancellation rather than unknown",
            (
                "caller-driven cancellation remains visible in telemetry without "
                "being marked"
            ),
            "## Workflow And Release Review",
            "public package index publication is out of scope until pilot approval",
            "## Pilot Security Controls",
            "## No-Go Rules",
            "rollback target is unknown",
            "## Reviewer Sign-Off Template",
            "Decision | Pending / Approved / Changes required",
        )
    )


def _write_fixture(root: Path) -> None:
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance" / "security-review.md").write_text(
        _security_review_doc(),
        encoding="utf-8",
    )
    (root / "SECURITY.md").write_text(
        "\n".join(
            (
                "## Threat Model",
                "## Deployment Threat Model",
                "## Release Artifact Verification",
                "docs/compliance/security-review.md",
            )
        ),
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Security Review: compliance/security-review.md",
        encoding="utf-8",
    )
    for workflow in ("ci.yml", "release.yml"):
        (root / ".github" / "workflows" / workflow).write_text(
            "python scripts/check_security_review.py",
            encoding="utf-8",
        )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "docs/compliance/security-review.md\n"
        "scripts/check_security_review.py\n"
        "caller-driven cancellation does not set OTel `error.type`",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-readiness-summary.md").write_text(
        "security review checklist\n"
        "security review checklist is complete and signed off before private RC\n"
        "Security review | Open",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a security review checklist and guard.\n"
        "Security review may miss runtime-specific risks | Mitigated\n"
        "Cancellation OTel non-error validation passed.\n"
        "Security review guard passed.",
        encoding="utf-8",
    )


def test_security_review_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_security_review_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_security_review_rejects_missing_no_go_rule(tmp_path: Path) -> None:
    module = _load_security_review_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "security-review.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace("rollback target is unknown", ""),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("rollback target" in failure for failure in failures)


def test_security_review_rejects_missing_security_policy_pointer(
    tmp_path: Path,
) -> None:
    module = _load_security_review_module()
    _write_fixture(tmp_path)
    (tmp_path / "SECURITY.md").write_text("## Threat Model", encoding="utf-8")

    failures = module._failures(tmp_path)
    assert any("SECURITY.md" in failure for failure in failures)


def test_security_review_rejects_missing_workflow_guard(tmp_path: Path) -> None:
    module = _load_security_review_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("", encoding="utf-8")

    failures = module._failures(tmp_path)
    assert any("ci.yml" in failure for failure in failures)


def test_security_review_rejects_missing_inventory_status(tmp_path: Path) -> None:
    module = _load_security_review_module()
    _write_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "rc-inventory.md").write_text(
        "Add a security review checklist and guard.",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("runtime-specific" in failure for failure in failures)


def test_security_review_rejects_missing_cancellation_telemetry_control(
    tmp_path: Path,
) -> None:
    module = _load_security_review_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "security-review.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            (
                "caller-driven cancellation remains visible in telemetry without "
                "being marked"
            ),
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("cancellation remains visible" in failure for failure in failures)


def test_security_review_skips_preflight_parity_for_clean_ci_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_security_review_module()
    _write_fixture(tmp_path)
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(module, "_git_diff_stats", lambda root: ((0, 0, 0), []))
    monkeypatch.setattr(module, "_git_untracked_count", lambda root: (0, []))

    assert module._check_preflight_snapshot_parity(tmp_path) == []


def test_security_review_accepts_matching_preflight_parity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_security_review_module()
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


def test_security_review_rejects_branch_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_security_review_module()
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
        "security review branch preflight drift: "
        "documents production-rc-0.3.0, current is main"
    ]


def test_security_review_rejects_release_diff_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_security_review_module()
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
        "security review release diff preflight drift: "
        "documents (85, 8859, 1794), current is (86, 8859, 1794)"
    ]


def test_security_review_rejects_untracked_preflight_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_security_review_module()
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
        "security review untracked asset preflight drift: documents 104, current is 105"
    ]
