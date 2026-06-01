from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_staging_manifest_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_staging_manifest.py"
    )
    spec = importlib.util.spec_from_file_location("check_staging_manifest", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(*snippets: str) -> str:
    return "\n\n".join(snippets)


def _snapshot_table(total: int = 11) -> str:
    return "\n".join(
        (
            "| Category | Count | Status |",
            "|---|---:|---|",
            "| `docs/api/*.md` | 1 | Allowed |",
            "| `docs/compliance/*.md` | 1 | Allowed |",
            "| `docs/patterns/pilot-profile.md` | 1 | Allowed |",
            "| `docs/styles/*.css` | 1 | Allowed |",
            "| `docs_theme/*.html` | 1 | Allowed |",
            "| `scripts/check_*.py` | 1 | Allowed |",
            "| `scripts/release_preflight.py` | 1 | Allowed |",
            "| `scripts/install_toolchain.py` | 1 | Allowed |",
            "| `scripts/mkdocs_hooks/*.py` | 1 | Allowed |",
            "| `src/techrevati/runtime/pilot.py` | 1 | Allowed |",
            "| `tests/test_*.py` release guard/test files | 1 | Allowed |",
            f"| Total | {total} | Pending reviewer confirmation |",
        )
    )


def _allowed_snapshot_untracked() -> list[str]:
    return [
        "docs/api/pilot.md",
        "docs/compliance/staging-manifest.md",
        "docs/patterns/pilot-profile.md",
        "docs/styles/runtime.css",
        "docs_theme/main.html",
        "scripts/check_staging_manifest.py",
        "scripts/release_preflight.py",
        "scripts/install_toolchain.py",
        "scripts/mkdocs_hooks/remove_generator_meta.py",
        "src/techrevati/runtime/pilot.py",
        "tests/test_staging_manifest.py",
    ]


def _write_fixture(root: Path) -> None:
    module = _load_staging_manifest_module()
    compliance = root / "docs" / "compliance"
    workflows = root / ".github" / "workflows"
    scripts = root / "scripts"
    compliance.mkdir(parents=True)
    workflows.mkdir(parents=True)
    scripts.mkdir()

    (compliance / "staging-manifest.md").write_text(
        _text(
            "# Staging Manifest",
            *module.REQUIRED_SECTIONS,
            *module.DOC_REQUIRED_SNIPPETS,
            _snapshot_table(),
        ),
        encoding="utf-8",
    )
    (compliance / "final-diff-review.md").write_text(
        _text("# Final Diff Review", *module.FINAL_REVIEW_REQUIRED_SNIPPETS),
        encoding="utf-8",
    )
    (compliance / "rc-review-handoff.md").write_text(
        _text("# RC Review Handoff", *module.HANDOFF_REQUIRED_SNIPPETS),
        encoding="utf-8",
    )
    (compliance / "rc-inventory.md").write_text(
        _text("# Release Candidate Inventory", *module.INVENTORY_REQUIRED_SNIPPETS),
        encoding="utf-8",
    )
    (compliance / "production-readiness.md").write_text(
        _text("# Production Readiness", *module.PLAN_REQUIRED_SNIPPETS),
        encoding="utf-8",
    )
    (compliance / "guard-calibration.md").write_text(
        _text("# Guard Calibration", *module.GUARD_CALIBRATION_REQUIRED_SNIPPETS),
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Staging Manifest: compliance/staging-manifest.md",
        encoding="utf-8",
    )
    for workflow in ("ci.yml", "release.yml"):
        (workflows / workflow).write_text(
            "python scripts/check_staging_manifest.py",
            encoding="utf-8",
        )
    (scripts / "check_ci_guardrails.py").write_text(
        '"check_staging_manifest.py"',
        encoding="utf-8",
    )


def test_staging_manifest_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_staging_manifest_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_staging_manifest_rejects_missing_manifest_snippet(
    tmp_path: Path,
) -> None:
    module = _load_staging_manifest_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "staging-manifest.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "git ls-files --others --exclude-standard",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any(
        "git ls-files --others --exclude-standard" in failure for failure in failures
    )


def test_staging_manifest_rejects_missing_workflow_guard(tmp_path: Path) -> None:
    module = _load_staging_manifest_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        "jobs: {}\n",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("release.yml" in failure for failure in failures)


def test_staging_manifest_accepts_allowed_untracked_categories() -> None:
    module = _load_staging_manifest_module()

    assert (
        module._check_untracked_files(
            [
                "docs/api/pilot.md",
                "docs/compliance/staging-manifest.md",
                "docs/patterns/pilot-profile.md",
                "docs/styles/runtime.css",
                "docs_theme/main.html",
                "scripts/check_staging_manifest.py",
                "scripts/release_preflight.py",
                "scripts/install_toolchain.py",
                "scripts/mkdocs_hooks/remove_generator_meta.py",
                "src/techrevati/runtime/pilot.py",
                "tests/test_staging_manifest.py",
            ]
        )
        == []
    )


def test_staging_manifest_rejects_unclassified_untracked_file() -> None:
    module = _load_staging_manifest_module()

    failures = module._check_untracked_files(["notes/local-release.txt"])

    assert failures == [
        "untracked file is not classified for staging: notes/local-release.txt"
    ]


def test_staging_manifest_rejects_generated_untracked_file() -> None:
    module = _load_staging_manifest_module()

    failures = module._check_untracked_files(
        ["docs/__pycache__/stale.pyc", "dist/package.whl", ".coverage"]
    )

    assert failures == [
        "generated/local artifact is untracked: .coverage",
        "generated/local artifact is untracked: dist/package.whl",
        "generated/local artifact is untracked: docs/__pycache__/stale.pyc",
    ]


def test_staging_manifest_accepts_matching_snapshot_counts() -> None:
    module = _load_staging_manifest_module()

    assert (
        module._check_snapshot_counts(
            _snapshot_table(),
            _allowed_snapshot_untracked(),
        )
        == []
    )


def test_staging_manifest_rejects_snapshot_count_drift() -> None:
    module = _load_staging_manifest_module()

    failures = module._check_snapshot_counts(
        _snapshot_table(total=10),
        _allowed_snapshot_untracked(),
    )

    assert failures == [
        "staging manifest snapshot count drift: Total documents 10, current is 11"
    ]
