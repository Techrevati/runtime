from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_operations_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_operations_runbook.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_operations_runbook", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_runbook(module: ModuleType) -> str:
    snippets = (
        "# Pilot Operations Runbook",
        *module.REQUIRED_SECTIONS,
        *module.REQUIRED_SIGNALS,
        *module.REQUIRED_ALERTS,
        *module.REQUIRED_WIRING,
        *module.REQUIRED_PROCEDURES,
        *module.REQUIRED_QUERIES,
    )
    return "\n\n".join(snippets)


def _write_fixture(tmp_path: Path, *, runbook: str | None = None) -> None:
    module = _load_operations_module()
    (tmp_path / "docs" / "compliance").mkdir(parents=True)
    (tmp_path / "docs" / "compliance" / "pilot-operations-runbook.md").write_text(
        _valid_runbook(module) if runbook is None else runbook,
        encoding="utf-8",
    )
    (tmp_path / "mkdocs.yml").write_text(
        "\n".join(
            (
                "nav:",
                "  - Compliance:",
                "    - Pilot Operations Runbook: "
                "compliance/pilot-operations-runbook.md",
            )
        ),
        encoding="utf-8",
    )


def test_operations_runbook_accepts_expected_content(tmp_path: Path) -> None:
    module = _load_operations_module()
    _write_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_operations_runbook_rejects_missing_signal(tmp_path: Path) -> None:
    module = _load_operations_module()
    _write_fixture(
        tmp_path,
        runbook=_valid_runbook(module).replace("Permission denials", ""),
    )

    failures = module._failures(tmp_path)

    assert any("Permission denials" in failure for failure in failures)


def test_operations_runbook_rejects_missing_alert(tmp_path: Path) -> None:
    module = _load_operations_module()
    _write_fixture(
        tmp_path,
        runbook=_valid_runbook(module).replace("OTel export failure", ""),
    )

    failures = module._failures(tmp_path)

    assert any("OTel export failure" in failure for failure in failures)


def test_operations_runbook_rejects_missing_wiring(tmp_path: Path) -> None:
    module = _load_operations_module()
    _write_fixture(
        tmp_path,
        runbook=_valid_runbook(module).replace("FanoutEventSink", ""),
    )

    failures = module._failures(tmp_path)

    assert any("FanoutEventSink" in failure for failure in failures)


def test_operations_runbook_rejects_missing_nav(tmp_path: Path) -> None:
    module = _load_operations_module()
    _write_fixture(tmp_path)
    (tmp_path / "mkdocs.yml").write_text("nav:\n", encoding="utf-8")

    failures = module._failures(tmp_path)

    assert any("mkdocs.yml" in failure for failure in failures)
