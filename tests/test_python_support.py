from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_python_support_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_python_support.py"
    )
    spec = importlib.util.spec_from_file_location("check_python_support", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pyproject(**project_overrides: Any) -> dict[str, Any]:
    project = {
        "requires-python": ">=3.11",
        "classifiers": [
            "Programming Language :: Python :: 3.11",
            "Programming Language :: Python :: 3.12",
            "Programming Language :: Python :: 3.13",
            "Typing :: Typed",
        ],
    }
    project.update(project_overrides)
    return {
        "project": project,
        "tool": {
            "mypy": {"python_version": "3.11"},
            "ruff": {"target-version": "py311"},
        },
    }


def test_python_support_accepts_expected_pyproject() -> None:
    module = _load_python_support_module()
    assert module._check_pyproject(_pyproject()) == []


def test_python_support_rejects_requires_python_drift() -> None:
    module = _load_python_support_module()
    failures = module._check_pyproject(_pyproject(**{"requires-python": ">=3.12"}))
    assert any("requires-python" in failure for failure in failures)


def test_python_support_rejects_classifier_drift() -> None:
    module = _load_python_support_module()
    failures = module._check_pyproject(
        _pyproject(
            classifiers=[
                "Programming Language :: Python :: 3.11",
                "Programming Language :: Python :: 3.12",
            ]
        )
    )
    assert any("classifiers" in failure for failure in failures)


def test_python_support_rejects_tool_baseline_drift() -> None:
    module = _load_python_support_module()
    pyproject = _pyproject()
    pyproject["tool"]["mypy"]["python_version"] = "3.12"
    pyproject["tool"]["ruff"]["target-version"] = "py312"

    failures = module._check_pyproject(pyproject)
    assert any("mypy" in failure for failure in failures)
    assert any("ruff" in failure for failure in failures)


def test_python_support_extracts_workflow_versions() -> None:
    module = _load_python_support_module()
    text = """
with:
  python-version: '3.11'
matrix:
  python-version: ['3.11', '3.12', '3.13']
"""
    assert module._literal_python_versions(text) == [
        ("3.11",),
        ("3.11", "3.12", "3.13"),
    ]
