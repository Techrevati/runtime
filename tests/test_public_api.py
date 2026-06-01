from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_public_api_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_public_api.py"
    )
    spec = importlib.util.spec_from_file_location("check_public_api", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALID_INIT = """
from techrevati.runtime.alpha import Alpha, beta
from techrevati.runtime.gamma import Gamma

__version__ = "1.2.3"
__all__ = ["Alpha", "Gamma", "__version__", "beta"]
"""
VALID_EXPORTS = ["Alpha", "Gamma", "__version__", "beta"]


def test_public_api_accepts_matching_exports() -> None:
    module = _load_public_api_module()
    assert module._check_public_api(VALID_INIT, expected_exports=VALID_EXPORTS) == []


def test_public_api_rejects_missing_export() -> None:
    module = _load_public_api_module()
    failures = module._check_public_api(
        VALID_INIT.replace('"Gamma", ', ""),
        expected_exports=VALID_EXPORTS,
    )
    assert any("Gamma" in failure for failure in failures)


def test_public_api_rejects_unknown_export() -> None:
    module = _load_public_api_module()
    failures = module._check_public_api(
        VALID_INIT.replace('"beta"', '"beta", "Missing"'),
        expected_exports=VALID_EXPORTS,
    )
    assert any("Missing" in failure for failure in failures)


def test_public_api_rejects_duplicate_export() -> None:
    module = _load_public_api_module()
    failures = module._check_public_api(
        VALID_INIT.replace('"beta"', '"beta", "beta"'),
        expected_exports=VALID_EXPORTS,
    )
    assert any("duplicate" in failure for failure in failures)


def test_public_api_rejects_non_literal_all() -> None:
    module = _load_public_api_module()
    failures = module._check_public_api(
        "__all__ = tuple(['Alpha'])\n",
        expected_exports=VALID_EXPORTS,
    )
    assert any("literal list" in failure for failure in failures)


def test_public_api_rejects_export_order_changes() -> None:
    module = _load_public_api_module()
    reordered = VALID_INIT.replace(
        '["Alpha", "Gamma", "__version__", "beta"]',
        '["Gamma", "Alpha", "__version__", "beta"]',
    )
    failures = module._check_public_api(reordered, expected_exports=VALID_EXPORTS)
    assert any("export order changed" in failure for failure in failures)


def test_public_api_rejects_exports_outside_frozen_set() -> None:
    module = _load_public_api_module()
    failures = module._check_public_api(
        VALID_INIT.replace('"beta"', '"beta", "NewExport"'),
        expected_exports=VALID_EXPORTS,
    )
    assert any("outside frozen set" in failure for failure in failures)


def test_current_runtime_init_matches_frozen_public_api() -> None:
    module = _load_public_api_module()
    init_file = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "techrevati"
        / "runtime"
        / "__init__.py"
    )

    assert module._check_public_api(init_file.read_text(encoding="utf-8")) == []
