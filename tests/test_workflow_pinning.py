from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_workflow_pinning_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_workflow_pinning.py"
    )
    spec = importlib.util.spec_from_file_location("check_workflow_pinning", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pinning_accepts_bare_sha_ref() -> None:
    module = _load_workflow_pinning_module()
    assert module._is_pinned("actions/checkout@" + ("a" * 40))


def test_pinning_accepts_quoted_sha_ref() -> None:
    # Regression: a correctly SHA-pinned action wrapped in YAML quotes must not
    # be falsely rejected.
    module = _load_workflow_pinning_module()
    sha = "actions/checkout@" + ("a" * 40)
    assert module._is_pinned(f'"{sha}"')
    assert module._is_pinned(f"'{sha}'")


def test_pinning_accepts_local_action() -> None:
    module = _load_workflow_pinning_module()
    assert module._is_pinned("./.github/actions/local")
    assert module._is_pinned('"./.github/actions/local"')


def test_pinning_rejects_tag_ref() -> None:
    module = _load_workflow_pinning_module()
    assert not module._is_pinned("actions/checkout@v4")
    assert not module._is_pinned('"actions/checkout@v4"')
