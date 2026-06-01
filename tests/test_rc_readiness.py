from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_rc_readiness_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_rc_readiness.py"
    )
    spec = importlib.util.spec_from_file_location("check_rc_readiness", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _text(*snippets: str) -> str:
    return "\n\n".join(snippets)


def _write_fixture(tmp_path: Path) -> None:
    module = _load_rc_readiness_module()
    compliance = tmp_path / "docs" / "compliance"
    compliance.mkdir(parents=True)
    (compliance / "rc-readiness-summary.md").write_text(
        _text(
            "# Release Candidate Readiness Summary",
            *module.SUMMARY_REQUIRED_SECTIONS,
            *module.SUMMARY_REQUIRED_SNIPPETS,
        ),
        encoding="utf-8",
    )
    (compliance / "rc-inventory.md").write_text(
        _text(
            "# Release Candidate Inventory",
            *module.INVENTORY_REQUIRED_SNIPPETS,
        ),
        encoding="utf-8",
    )
    (compliance / "production-readiness.md").write_text(
        _text(
            "# Production Readiness Plan",
            *module.PLAN_REQUIRED_SNIPPETS,
        ),
        encoding="utf-8",
    )
    (tmp_path / "mkdocs.yml").write_text(
        _text(
            "nav:",
            "  - Compliance:",
            "    - RC Readiness Summary: compliance/rc-readiness-summary.md",
        ),
        encoding="utf-8",
    )
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for workflow in ("ci.yml", "release.yml"):
        (workflows / workflow).write_text(
            "run: python scripts/check_rc_readiness.py\n",
            encoding="utf-8",
        )


def test_rc_readiness_accepts_expected_fixture(tmp_path: Path) -> None:
    module = _load_rc_readiness_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_rc_readiness_rejects_missing_stable_blocker(tmp_path: Path) -> None:
    module = _load_rc_readiness_module()
    _write_fixture(tmp_path)
    summary = tmp_path / "docs" / "compliance" / "rc-readiness-summary.md"
    summary.write_text(
        summary.read_text(encoding="utf-8").replace("rollback proof", ""),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("rollback proof" in failure for failure in failures)


def test_rc_readiness_rejects_checked_real_pilot_item(tmp_path: Path) -> None:
    module = _load_rc_readiness_module()
    _write_fixture(tmp_path)
    inventory = tmp_path / "docs" / "compliance" / "rc-inventory.md"
    inventory.write_text(
        inventory.read_text(encoding="utf-8").replace(
            "- [ ] Execute the real controlled RC pilot.",
            "- [x] Execute the real controlled RC pilot.",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any(
        "Execute the real controlled RC pilot" in failure for failure in failures
    )


def test_rc_readiness_rejects_missing_workflow_guard(tmp_path: Path) -> None:
    module = _load_rc_readiness_module()
    _write_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        "jobs: {}\n",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("release.yml" in failure for failure in failures)
    assert any("check_rc_readiness.py" in failure for failure in failures)


def test_rc_readiness_rejects_missing_nav(tmp_path: Path) -> None:
    module = _load_rc_readiness_module()
    _write_fixture(tmp_path)
    (tmp_path / "mkdocs.yml").write_text("nav:\n", encoding="utf-8")

    failures = module._failures(tmp_path)

    assert any("mkdocs.yml" in failure for failure in failures)


def test_rc_readiness_rejects_weakened_security_boundary(tmp_path: Path) -> None:
    module = _load_rc_readiness_module()
    _write_fixture(tmp_path)
    summary = tmp_path / "docs" / "compliance" / "rc-readiness-summary.md"
    summary.write_text(
        summary.read_text(encoding="utf-8")
        + "\nsecurity review checklist is complete or explicitly still pending",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("weakened boundary" in failure for failure in failures)


def test_rc_readiness_rejects_missing_cancellation_otel_evidence(
    tmp_path: Path,
) -> None:
    module = _load_rc_readiness_module()
    _write_fixture(tmp_path)
    inventory = tmp_path / "docs" / "compliance" / "rc-inventory.md"
    inventory.write_text(
        inventory.read_text(encoding="utf-8").replace(
            "Cancellation OTel non-error validation passed.",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("Cancellation OTel" in failure for failure in failures)
