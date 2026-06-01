from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = (
    "tiny_agent.py",
    "parallel_tools.py",
    "durable_agent.py",
)


def _example_env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = (
        src if not env.get("PYTHONPATH") else f"{src}{os.pathsep}{env['PYTHONPATH']}"
    )
    return env


def test_examples_run_without_extra_dependencies(tmp_path: Path) -> None:
    for example in EXAMPLES:
        result = subprocess.run(
            [sys.executable, str(ROOT / "examples" / example)],
            cwd=tmp_path,
            env=_example_env(),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"{example} failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
