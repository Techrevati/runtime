from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_pilot_evidence_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_pilot_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("check_pilot_evidence", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_pilot_template(module: ModuleType) -> str:
    snippets = (
        "# Controlled RC Pilot Evidence Template",
        *module.PILOT_REQUIRED_SECTIONS,
        *module.PILOT_REQUIRED_SCENARIOS,
        *module.PILOT_REQUIRED_SIGNALS,
        *module.PILOT_REQUIRED_CRITERIA,
        *module.PILOT_REQUIRED_PRECONDITIONS,
    )
    return "\n\n".join(snippets)


def _valid_rollback_template(module: ModuleType) -> str:
    snippets = (
        "# Rollback Proof Checklist",
        *module.ROLLBACK_REQUIRED_SECTIONS,
        *module.ROLLBACK_REQUIRED_SNIPPETS,
    )
    return "\n\n".join(snippets)


def _write_fixture(
    tmp_path: Path,
    *,
    pilot: str | None = None,
    rollback: str | None = None,
) -> None:
    module = _load_pilot_evidence_module()
    compliance = tmp_path / "docs" / "compliance"
    compliance.mkdir(parents=True)
    (compliance / "pilot-evidence-template.md").write_text(
        _valid_pilot_template(module) if pilot is None else pilot,
        encoding="utf-8",
    )
    (compliance / "rollback-proof-checklist.md").write_text(
        _valid_rollback_template(module) if rollback is None else rollback,
        encoding="utf-8",
    )
    (tmp_path / "mkdocs.yml").write_text(
        "\n".join(
            (
                "nav:",
                "  - Compliance:",
                "    - Controlled RC Pilot Evidence: "
                "compliance/pilot-evidence-template.md",
                "    - Rollback Proof Checklist: "
                "compliance/rollback-proof-checklist.md",
            )
        ),
        encoding="utf-8",
    )


def test_pilot_evidence_accepts_expected_templates(tmp_path: Path) -> None:
    module = _load_pilot_evidence_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_pilot_evidence_rejects_missing_scenario(tmp_path: Path) -> None:
    module = _load_pilot_evidence_module()
    _write_fixture(
        tmp_path,
        pilot=_valid_pilot_template(module).replace("Provider failover", ""),
    )

    failures = module._failures(tmp_path)

    assert any("Provider failover" in failure for failure in failures)


def test_pilot_evidence_rejects_missing_signal(tmp_path: Path) -> None:
    module = _load_pilot_evidence_module()
    _write_fixture(
        tmp_path,
        pilot=_valid_pilot_template(module).replace("Tool-call latency", ""),
    )

    failures = module._failures(tmp_path)

    assert any("Tool-call latency" in failure for failure in failures)


def test_pilot_evidence_rejects_missing_rollback_command(tmp_path: Path) -> None:
    module = _load_pilot_evidence_module()
    _write_fixture(
        tmp_path,
        rollback=_valid_rollback_template(module).replace(
            "TECHREVATI_RUNTIME_ROLLBACK_VERSION", ""
        ),
    )

    failures = module._failures(tmp_path)

    assert any("TECHREVATI_RUNTIME_ROLLBACK_VERSION" in failure for failure in failures)


def test_pilot_evidence_rejects_missing_nav(tmp_path: Path) -> None:
    module = _load_pilot_evidence_module()
    _write_fixture(tmp_path)
    (tmp_path / "mkdocs.yml").write_text("nav:\n", encoding="utf-8")

    failures = module._failures(tmp_path)

    assert any("pilot-evidence-template.md" in failure for failure in failures)
    assert any("rollback-proof-checklist.md" in failure for failure in failures)
