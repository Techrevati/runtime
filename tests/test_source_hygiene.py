from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType


def _load_source_hygiene_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_source_hygiene.py"
    )
    spec = importlib.util.spec_from_file_location("check_source_hygiene", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_hygiene_accepts_clean_code() -> None:
    module = _load_source_hygiene_module()
    code = "def run() -> int:\n    return 1\n"

    assert module._check_ast(Path("clean.py"), ast.parse(code)) == []
    assert module._check_comments(Path("clean.py"), code) == []


def test_source_hygiene_ignores_docstring_examples() -> None:
    module = _load_source_hygiene_module()
    code = '"""Example:\nprint("ok")\n"""\n'

    assert module._check_ast(Path("docs.py"), ast.parse(code)) == []


def test_source_hygiene_rejects_debug_calls_and_imports() -> None:
    module = _load_source_hygiene_module()
    code = "\n".join(
        (
            "import pdb",
            "def run() -> None:",
            "    print('debug')",
            "    breakpoint()",
            "    pdb.set_trace()",
        )
    )

    failures = module._check_ast(Path("debug.py"), ast.parse(code))
    assert any("import" in failure for failure in failures)
    assert sum("debug call" in failure for failure in failures) == 3


def test_source_hygiene_allows_cli_prints_in_scripts() -> None:
    module = _load_source_hygiene_module()
    code = "\n".join(
        (
            "def main() -> int:",
            "    print('check OK')",
            "    return 0",
        )
    )

    failures = module._check_ast(Path("scripts/check_example.py"), ast.parse(code))

    assert failures == []


def test_source_hygiene_still_rejects_breakpoint_in_scripts() -> None:
    module = _load_source_hygiene_module()
    code = "def main() -> int:\n    breakpoint()\n    return 0\n"

    failures = module._check_ast(Path("scripts/check_example.py"), ast.parse(code))

    assert any("breakpoint" in failure for failure in failures)


def test_source_hygiene_scans_multiple_roots_with_cli_script_calibration(
    tmp_path: Path,
) -> None:
    module = _load_source_hygiene_module()
    src = tmp_path / "src"
    scripts = tmp_path / "scripts"
    tests = tmp_path / "tests"
    src.mkdir()
    scripts.mkdir()
    tests.mkdir()
    (src / "app.py").write_text("print('debug')\n", encoding="utf-8")
    (scripts / "check_example.py").write_text(
        "def main() -> int:\n    print('check OK')\n    return 0\n",
        encoding="utf-8",
    )
    (tests / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n",
        encoding="utf-8",
    )

    failures = module._failures((src, scripts, tests))

    assert failures == [f"{src / 'app.py'}:1: forbidden debug call 'print'"]


def test_source_hygiene_skips_generated_and_cache_directories(tmp_path: Path) -> None:
    module = _load_source_hygiene_module()
    src = tmp_path / "src"
    cache = src / "__pycache__"
    build = src / "build"
    cache.mkdir(parents=True)
    build.mkdir()
    clean = src / "clean.py"
    cached = cache / "cached.py"
    generated = build / "generated.py"
    clean.write_text("VALUE = 1\n", encoding="utf-8")
    cached.write_text("print('debug')\n", encoding="utf-8")
    generated.write_text("print('debug')\n", encoding="utf-8")

    files = list(module._source_files(src))

    assert files == [clean]


def test_source_hygiene_rejects_bare_except() -> None:
    module = _load_source_hygiene_module()
    code = "try:\n    run()\nexcept:\n    recover()\n"

    failures = module._check_ast(Path("bare.py"), ast.parse(code))
    assert any("bare except" in failure for failure in failures)


def test_source_hygiene_rejects_not_implemented_stubs() -> None:
    module = _load_source_hygiene_module()
    code = "\n".join(
        (
            "class Base:",
            "    def run(self) -> None:",
            "        raise NotImplementedError",
        )
    )

    failures = module._check_ast(Path("stub.py"), ast.parse(code))

    assert any("abc.abstractmethod" in failure for failure in failures)


def test_source_hygiene_rejects_text_io_without_encoding() -> None:
    module = _load_source_hygiene_module()
    code = "\n".join(
        (
            "from pathlib import Path",
            "def write(path: Path) -> None:",
            "    path.read_text()",
            "    path.write_text('data')",
            "    open(path, 'w')",
            "    path.open('r')",
            "    path.read_text(encoding='utf-8')",
            "    path.write_text('data', encoding='utf-8')",
            "    open(path, 'rb')",
            "    path.open('wb')",
        )
    )

    failures = module._check_ast(Path("io.py"), ast.parse(code))

    assert sum("text I/O must set encoding" in failure for failure in failures) == 4


def test_source_hygiene_rejects_comment_markers() -> None:
    module = _load_source_hygiene_module()
    failures = module._check_comments(Path("comment.py"), "# TODO: finish\n")

    assert any("TODO" in failure for failure in failures)
