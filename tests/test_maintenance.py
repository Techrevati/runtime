from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_maintenance_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_maintenance.py"
    )
    spec = importlib.util.spec_from_file_location("check_maintenance", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_CONFIG = """
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
      day: monday
    open-pull-requests-limit: 5
    labels:
      - dependencies
      - python
    groups:
      dev-tools:
        patterns:
          - "ruff"
          - "mypy"
          - "pytest*"
      otel:
        patterns:
          - "opentelemetry-*"

  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: weekly
      day: monday
    open-pull-requests-limit: 3
    labels:
      - dependencies
      - github-actions
"""


def test_maintenance_accepts_expected_config() -> None:
    module = _load_maintenance_module()
    assert module._check_config(VALID_CONFIG) == []


def test_maintenance_rejects_missing_version() -> None:
    module = _load_maintenance_module()
    failures = module._check_config(VALID_CONFIG.replace("version: 2\n", ""))
    assert any("version" in failure for failure in failures)


def test_maintenance_rejects_missing_update_block() -> None:
    module = _load_maintenance_module()
    config = VALID_CONFIG.replace("  - package-ecosystem: github-actions", "")
    failures = module._check_config(config)
    assert any("github-actions" in failure for failure in failures)


def test_maintenance_rejects_missing_required_policy() -> None:
    module = _load_maintenance_module()
    config = VALID_CONFIG.replace("      day: monday\n", "", 1)
    failures = module._check_config(config)
    assert any("day: monday" in failure for failure in failures)
