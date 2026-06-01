from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_workflow_hardening_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_workflow_hardening.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_workflow_hardening",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workflow_hardening_accepts_hardened_workflow() -> None:
    module = _load_workflow_hardening_module()
    workflow = """
name: CI

on:
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@abc123
        with:
          persist-credentials: false
      - run: true
"""

    assert module._check_workflow(Path("ci.yml"), workflow) == []


def test_workflow_hardening_rejects_pull_request_target() -> None:
    module = _load_workflow_hardening_module()
    workflow = """
name: Unsafe

on:
  pull_request_target:

permissions:
  contents: read

concurrency:
  group: unsafe

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - run: true
"""

    failures = module._check_workflow(Path("unsafe.yml"), workflow)
    assert any("pull_request_target" in failure for failure in failures)


def test_workflow_hardening_rejects_missing_top_level_controls() -> None:
    module = _load_workflow_hardening_module()
    workflow = """
name: CI

on:
  push:

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - run: true
"""

    failures = module._check_workflow(Path("ci.yml"), workflow)
    assert any("permissions" in failure for failure in failures)
    assert any("concurrency" in failure for failure in failures)


def test_workflow_hardening_rejects_job_without_timeout() -> None:
    module = _load_workflow_hardening_module()
    workflow = """
name: CI

on:
  push:

permissions:
  contents: read

concurrency:
  group: ci

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: true
"""

    failures = module._check_workflow(Path("ci.yml"), workflow)
    assert any("timeout-minutes" in failure for failure in failures)


def test_workflow_hardening_rejects_too_large_timeout() -> None:
    module = _load_workflow_hardening_module()
    workflow = """
name: CI

on:
  push:

permissions:
  contents: read

concurrency:
  group: ci

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    steps:
      - run: true
"""

    failures = module._check_workflow(Path("ci.yml"), workflow)
    assert any("between 1 and 30" in failure for failure in failures)


def test_workflow_hardening_rejects_invalid_timeout_value() -> None:
    module = _load_workflow_hardening_module()
    workflow = """
name: CI

on:
  push:

permissions:
  contents: read

concurrency:
  group: ci

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: ${{ vars.TIMEOUT }}
    steps:
      - run: true
"""

    failures = module._check_workflow(Path("ci.yml"), workflow)
    assert any("invalid timeout-minutes" in failure for failure in failures)


def test_workflow_hardening_rejects_checkout_with_persisted_credentials() -> None:
    module = _load_workflow_hardening_module()
    workflow = """
name: CI

on:
  push:

permissions:
  contents: read

concurrency:
  group: ci

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@abc123
      - run: true
"""

    failures = module._check_workflow(Path("ci.yml"), workflow)
    assert any("persist-credentials" in failure for failure in failures)
