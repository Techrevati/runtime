from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_precommit_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_precommit_config.py"
    )
    spec = importlib.util.spec_from_file_location("check_precommit_config", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pyproject() -> dict[str, Any]:
    return {
        "project": {
            "optional-dependencies": {
                "dev": [
                    "ruff==0.14.5",
                    "mypy==1.18.2",
                ]
            }
        }
    }


VALID_CONFIG = """
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.5
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: check-merge-conflict

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.18.2
    hooks:
      - id: mypy
        additional_dependencies: []
        args: [--strict]
"""


def test_precommit_accepts_expected_config() -> None:
    module = _load_precommit_module()
    assert module._check_precommit(VALID_CONFIG, _pyproject()) == []


def test_precommit_rejects_ruff_version_drift() -> None:
    module = _load_precommit_module()
    failures = module._check_precommit(
        VALID_CONFIG.replace("rev: v0.14.5", "rev: v0.14.4"),
        _pyproject(),
    )
    assert any("ruff-pre-commit" in failure for failure in failures)


def test_precommit_rejects_unpinned_revision() -> None:
    module = _load_precommit_module()
    failures = module._check_precommit(
        VALID_CONFIG.replace("rev: v4.5.0", "rev: main"),
        _pyproject(),
    )
    assert any("unpinned" in failure for failure in failures)


def test_precommit_rejects_missing_hook() -> None:
    module = _load_precommit_module()
    failures = module._check_precommit(
        VALID_CONFIG.replace("      - id: check-yaml\n", ""),
        _pyproject(),
    )
    assert any("check-yaml" in failure for failure in failures)


def test_precommit_rejects_missing_required_args() -> None:
    module = _load_precommit_module()
    config = VALID_CONFIG.replace("        args: [--fix]\n", "")
    failures = module._check_precommit(config, _pyproject())
    assert any("--fix" in failure for failure in failures)
