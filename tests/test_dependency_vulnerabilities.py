from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType


def _load_dependency_vulnerabilities_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "check_dependency_vulnerabilities.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_dependency_vulnerabilities", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_pyproject(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        """
[project]
dependencies = ["runtime-lib==1.0.0"]

[project.optional-dependencies]
dev = ["pytest==9.0.3", "ruff==0.14.5"]
docs = ["mkdocs==1.6.1"]
build = ["build==1.5.0"]
release = ["twine==6.2.0"]
otel = ["opentelemetry-api>=1.27,<2"]
""",
        encoding="utf-8",
    )


def test_dependency_audit_collects_runtime_and_toolchain_requirements(
    tmp_path: Path,
) -> None:
    module = _load_dependency_vulnerabilities_module()
    _write_pyproject(tmp_path)

    requirements = module._requirements_for_groups(tmp_path, ("dev", "otel"))

    assert requirements == [
        "opentelemetry-api>=1.27,<2",
        "pytest==9.0.3",
        "ruff==0.14.5",
        "runtime-lib==1.0.0",
    ]


def test_dependency_audit_rejects_missing_group(tmp_path: Path) -> None:
    module = _load_dependency_vulnerabilities_module()
    _write_pyproject(tmp_path)

    try:
        module._requirements_for_groups(tmp_path, ("missing",))
    except ValueError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_dependency_audit_accepts_clean_pip_audit_result(tmp_path: Path) -> None:
    module = _load_dependency_vulnerabilities_module()
    _write_pyproject(tmp_path)

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        assert "pip_audit" in args
        return subprocess.CompletedProcess(args, 0, "No known vulnerabilities found")

    assert module._audit_dependencies(tmp_path, runner=runner) == []


def test_dependency_audit_reports_vulnerabilities(tmp_path: Path) -> None:
    module = _load_dependency_vulnerabilities_module()
    _write_pyproject(tmp_path)

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "pkg CVE-0000-0000")

    failures = module._audit_dependencies(tmp_path, runner=runner)

    assert len(failures) == 1
    assert "CVE-0000-0000" in failures[0]


def test_dependency_audit_reports_missing_tool(tmp_path: Path) -> None:
    module = _load_dependency_vulnerabilities_module()
    _write_pyproject(tmp_path)

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "No module named pip_audit")

    failures = module._audit_dependencies(tmp_path, runner=runner)

    assert failures == [
        "pip-audit is not installed; run scripts/install_toolchain.py audit"
    ]
