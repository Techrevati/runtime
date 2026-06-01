"""Ensure public documentation only imports frozen package-level exports."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
import textwrap
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import ModuleType

PUBLIC_DOC_ROOTS = ("docs", "examples")
PUBLIC_MARKDOWN_FILES = ("README.md",)


def _load_public_api_module() -> ModuleType:
    module_path = Path(__file__).with_name("check_public_api.py")
    spec = importlib.util.spec_from_file_location("check_public_api", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frozen_runtime_exports() -> tuple[str, ...]:
    module = _load_public_api_module()
    return tuple(module.EXPECTED_RUNTIME_EXPORTS)


def _public_doc_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_file in PUBLIC_MARKDOWN_FILES:
        path = root / relative_file
        if path.is_file():
            files.append(path)

    for relative_root in PUBLIC_DOC_ROOTS:
        base = root / relative_root
        if not base.is_dir():
            continue
        files.extend(path for path in base.rglob("*") if path.suffix in {".md", ".py"})

    return sorted(files)


def _statement_lines(lines: Sequence[str], index: int) -> list[str]:
    statement = [lines[index]]
    balance = lines[index].count("(") - lines[index].count(")")
    while index + len(statement) < len(lines) and (
        balance > 0 or statement[-1].rstrip().endswith("\\")
    ):
        next_line = lines[index + len(statement)]
        statement.append(next_line)
        balance += next_line.count("(") - next_line.count(")")
    return statement


def _iter_package_imports(text: str) -> Iterable[tuple[int, ast.ImportFrom | None]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("from techrevati.runtime import"):
            continue

        statement = "\n".join(_statement_lines(lines, index))
        try:
            tree = ast.parse(textwrap.dedent(statement))
        except SyntaxError:
            yield index + 1, None
            continue

        import_from = tree.body[0] if tree.body else None
        if isinstance(import_from, ast.ImportFrom):
            yield index + 1, import_from
        else:
            yield index + 1, None


def _check_docs_public_api(
    root: Path,
    *,
    allowed_exports: Sequence[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    allowed = set(allowed_exports or _frozen_runtime_exports())

    for path in _public_doc_files(root):
        text = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(root).as_posix()
        for line_no, import_from in _iter_package_imports(text):
            if import_from is None:
                failures.append(
                    f"{relative_path}:{line_no} has an unparsable "
                    "techrevati.runtime package import"
                )
                continue
            if import_from.module != "techrevati.runtime":
                continue

            for alias in import_from.names:
                if alias.name == "*":
                    failures.append(
                        f"{relative_path}:{line_no} uses a wildcard import from "
                        "techrevati.runtime"
                    )
                elif alias.name not in allowed:
                    failures.append(
                        f"{relative_path}:{line_no} imports {alias.name!r} from "
                        "techrevati.runtime, but it is not in the frozen public API"
                    )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing public documentation.",
    )
    args = parser.parse_args()

    failures = _check_docs_public_api(args.root)
    if failures:
        print("documented public API check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Documented public API check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
