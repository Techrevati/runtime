from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_docs_public_api_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_docs_public_api.py"
    )
    spec = importlib.util.spec_from_file_location("check_docs_public_api", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_public_docs_fixture(root: Path, text: str) -> None:
    (root / "docs").mkdir()
    (root / "examples").mkdir()
    (root / "README.md").write_text(text, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(text, encoding="utf-8")
    (root / "examples" / "demo.py").write_text(text, encoding="utf-8")


def test_docs_public_api_accepts_frozen_package_imports(tmp_path: Path) -> None:
    module = _load_docs_public_api_module()
    _write_public_docs_fixture(
        tmp_path,
        "\n".join(
            (
                "```python",
                "from techrevati.runtime import AgentSession, UsageSnapshot",
                "from techrevati.runtime import (",
                "    CircuitBreaker,",
                "    RecoveryContext as Context,",
                ")",
                "from techrevati.runtime.policy_engine import QualityAt",
                "```",
            )
        ),
    )

    failures = module._check_docs_public_api(
        tmp_path,
        allowed_exports=(
            "AgentSession",
            "CircuitBreaker",
            "RecoveryContext",
            "UsageSnapshot",
        ),
    )

    assert failures == []


def test_docs_public_api_rejects_non_frozen_package_imports(tmp_path: Path) -> None:
    module = _load_docs_public_api_module()
    _write_public_docs_fixture(
        tmp_path,
        "from techrevati.runtime import AgentSession, InternalOnly\n",
    )

    failures = module._check_docs_public_api(
        tmp_path,
        allowed_exports=("AgentSession",),
    )

    assert any("InternalOnly" in failure for failure in failures)


def test_docs_public_api_rejects_wildcard_package_imports(tmp_path: Path) -> None:
    module = _load_docs_public_api_module()
    _write_public_docs_fixture(tmp_path, "from techrevati.runtime import *\n")

    failures = module._check_docs_public_api(
        tmp_path,
        allowed_exports=("AgentSession",),
    )

    assert any("wildcard" in failure for failure in failures)


def test_docs_public_api_rejects_unparsable_package_imports(tmp_path: Path) -> None:
    module = _load_docs_public_api_module()
    _write_public_docs_fixture(
        tmp_path,
        "\n".join(
            (
                "from techrevati.runtime import (",
                "    AgentSession,",
            )
        ),
    )

    failures = module._check_docs_public_api(
        tmp_path,
        allowed_exports=("AgentSession",),
    )

    assert any("unparsable" in failure for failure in failures)
