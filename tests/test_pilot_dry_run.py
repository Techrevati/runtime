from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _load_pilot_dry_run_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_pilot_dry_run.py"
    )
    spec = importlib.util.spec_from_file_location("check_pilot_dry_run", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_pilot_dry_run_passes_all_local_scenarios(tmp_path: Path) -> None:
    module = _load_pilot_dry_run_module()

    payload = module.run_pilot_dry_run(tmp_path)

    assert payload["passed"] is True
    assert payload["scenario_count"] == 10
    names = {scenario["name"] for scenario in payload["scenarios"]}
    assert names == {
        "successful session",
        "prompt-injection attempt",
        "permission denial",
        "guardrail block",
        "max-iterations breach",
        "max-tool-calls breach",
        "provider failover",
        "checkpoint resume",
        "sink failure diagnostic",
        "rollback readiness",
    }


def test_pilot_dry_run_writes_json_evidence(tmp_path: Path) -> None:
    module = _load_pilot_dry_run_module()
    output = tmp_path / "evidence" / "pilot-dry-run.json"

    payload = module.run_pilot_dry_run(tmp_path / "work")
    module._write_output(output, payload)

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["passed"] is True
    assert written["dry_run"] == "controlled_rc_pilot"


def test_pilot_dry_run_main_returns_zero_with_output(tmp_path: Path) -> None:
    module = _load_pilot_dry_run_module()
    output = tmp_path / "pilot-dry-run.json"

    exit_code = module.main(
        [
            "--work-dir",
            str(tmp_path / "work"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True
