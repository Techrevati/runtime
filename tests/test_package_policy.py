from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_package_policy_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_package_policy.py"
    )
    spec = importlib.util.spec_from_file_location("check_package_policy", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(**overrides: Any) -> dict[str, Any]:
    module = _load_package_policy_module()
    project = {
        **module.EXPECTED_PROJECT,
        "dependencies": [],
        "keywords": sorted(module.EXPECTED_KEYWORDS),
        "classifiers": sorted(module.REQUIRED_CLASSIFIERS),
        "optional-dependencies": {"otel": sorted(module.EXPECTED_OTEL_EXTRA)},
    }
    project.update(overrides)
    return project


def test_package_policy_accepts_expected_project() -> None:
    module = _load_package_policy_module()
    assert module._check_project(_project()) == []


def test_package_policy_rejects_runtime_dependencies() -> None:
    module = _load_package_policy_module()
    failures = module._check_project(_project(dependencies=["requests>=2"]))
    assert any("dependencies" in failure for failure in failures)


def test_package_policy_rejects_identity_drift() -> None:
    module = _load_package_policy_module()
    failures = module._check_project(_project(authors=[{"name": "Someone Else"}]))
    assert any("authors" in failure for failure in failures)


def test_package_policy_rejects_project_urls() -> None:
    module = _load_package_policy_module()
    failures = module._check_project(_project(urls={"Homepage": "https://example.com"}))
    assert any("urls" in failure for failure in failures)


def test_package_policy_rejects_otel_extra_drift() -> None:
    module = _load_package_policy_module()
    failures = module._check_project(
        _project(**{"optional-dependencies": {"otel": ["otel>=1"]}})
    )
    assert any("otel extra" in failure for failure in failures)


def test_package_policy_checks_source_files(tmp_path: Path) -> None:
    module = _load_package_policy_module()
    data_dir = tmp_path / "src" / "techrevati" / "runtime" / "data"
    data_dir.mkdir(parents=True)
    (tmp_path / "src" / "techrevati" / "runtime" / "py.typed").write_text(
        "",
        encoding="utf-8",
    )
    (data_dir / "pricing.json").write_text("{}", encoding="utf-8")

    assert module._check_source_files(tmp_path) == []
