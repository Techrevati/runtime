from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_repo_hygiene_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_repo_hygiene.py"
    )
    spec = importlib.util.spec_from_file_location("check_repo_hygiene", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_hygiene_accepts_required_gitignore_patterns() -> None:
    module = _load_repo_hygiene_module()
    text = "\n".join(module.REQUIRED_IGNORE_PATTERNS)

    assert module._check_gitignore(text) == []


def test_repo_hygiene_rejects_missing_gitignore_pattern() -> None:
    module = _load_repo_hygiene_module()
    text = "\n".join(
        pattern for pattern in module.REQUIRED_IGNORE_PATTERNS if pattern != "dist/"
    )

    failures = module._check_gitignore(text)
    assert any("dist/" in failure for failure in failures)


def test_repo_hygiene_rejects_tracked_generated_artifacts() -> None:
    module = _load_repo_hygiene_module()
    tracked = [
        "src/techrevati/runtime/__init__.py",
        "dist/package.whl",
        "site/index.html",
        "tests/__pycache__/test_runtime.cpython-312.pyc",
        ".coverage",
    ]

    failures = module._check_tracked_files(tracked)
    assert len(failures) == 4
    assert all("generated artifact" in failure for failure in failures)


def test_repo_hygiene_allows_normal_tracked_files() -> None:
    module = _load_repo_hygiene_module()
    tracked = [
        "src/techrevati/runtime/__init__.py",
        "docs/index.md",
        "scripts/check_repo_hygiene.py",
    ]

    assert module._check_tracked_files(tracked) == []
