from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_guard_calibration_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_guard_calibration.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_guard_calibration",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path) -> None:
    (root / "scripts").mkdir()
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    guard_names = ("check_a.py", "check_guard_calibration.py")
    for name in guard_names:
        (root / "scripts" / name).write_text("", encoding="utf-8")
    (root / "scripts" / "check_ci_guardrails.py").write_text(
        '"check_guard_calibration.py"',
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "guard-calibration.md").write_text(
        "\n".join(
            (
                "# Guard Calibration",
                "",
                "Guard calibration status: Pending until remote CI "
                "false-positive review is",
                "",
                "## Purpose",
                "## Guard Inventory",
                "`check_a.py`",
                "`check_guard_calibration.py`",
                "## False-Positive Handling",
                "Do not weaken a guard only to make CI green",
                "bug in guard",
                "bug in repository",
                "intentional policy exception",
                "high-risk controls and must not be bypassed",
                "## CI Parity",
                "Every verify-time guard must run in both the CI test job "
                "and the release",
                "`scripts/check_ci_guardrails.py` enforces the verify-time "
                "and special-case",
                "## Calibration Procedure",
                "## No-Go Rules",
                "guard passes locally but fails remote CI without triage",
                "## Sign-Off Template",
                "Decision | Pending / Approved / Changes required",
            )
        ),
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "rc-inventory.md").write_text(
        "\n".join(
            (
                "# Release Candidate Inventory",
                "New guard scripts may be too strict | Mitigated",
                "Guard calibration checklist and false-positive procedure",
            )
        ),
        encoding="utf-8",
    )
    (root / "mkdocs.yml").write_text(
        "Guard Calibration: compliance/guard-calibration.md",
        encoding="utf-8",
    )
    for workflow in ("ci.yml", "release.yml"):
        (root / ".github" / "workflows" / workflow).write_text(
            "python scripts/check_guard_calibration.py",
            encoding="utf-8",
        )


def test_guard_calibration_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_guard_calibration_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_guard_calibration_rejects_missing_guard_entry(tmp_path: Path) -> None:
    module = _load_guard_calibration_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "guard-calibration.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace("`check_a.py`\n", ""),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("check_a.py" in failure for failure in failures)


def test_guard_calibration_rejects_missing_false_positive_policy(
    tmp_path: Path,
) -> None:
    module = _load_guard_calibration_module()
    _write_fixture(tmp_path)
    doc = tmp_path / "docs" / "compliance" / "guard-calibration.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace(
            "Do not weaken a guard only to make CI green",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)
    assert any("weaken" in failure for failure in failures)


def test_guard_calibration_rejects_missing_workflow_guard(
    tmp_path: Path,
) -> None:
    module = _load_guard_calibration_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("", encoding="utf-8")

    failures = module._failures(tmp_path)
    assert any("ci.yml" in failure for failure in failures)


def test_guard_calibration_rejects_missing_nav_entry(tmp_path: Path) -> None:
    module = _load_guard_calibration_module()
    _write_fixture(tmp_path)
    (tmp_path / "mkdocs.yml").write_text("", encoding="utf-8")

    failures = module._failures(tmp_path)
    assert any("mkdocs.yml" in failure for failure in failures)


def test_guard_calibration_rejects_missing_ci_guardrail_classification(
    tmp_path: Path,
) -> None:
    module = _load_guard_calibration_module()
    _write_fixture(tmp_path)
    (tmp_path / "scripts" / "check_ci_guardrails.py").write_text("", encoding="utf-8")

    failures = module._failures(tmp_path)
    assert any("check_ci_guardrails.py" in failure for failure in failures)
