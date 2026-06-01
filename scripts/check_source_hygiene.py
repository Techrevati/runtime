"""Ensure production source does not carry debug leftovers."""

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from collections.abc import Iterable
from pathlib import Path

SKIPPED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "site",
}
FORBIDDEN_COMMENT_MARKERS = ("TODO", "FIXME", "HACK", "XXX")
FORBIDDEN_CALLS = {"breakpoint", "print"}
FORBIDDEN_IMPORT_ROOTS = {"pdb"}
FORBIDDEN_RAISES = {"NotImplementedError"}
TEXT_FILE_METHODS = {"read_text", "write_text"}
ARCHIVE_OPEN_ROOTS = {"gzip", "tarfile", "zipfile"}
CLI_SCRIPT_ROOTS = {"scripts"}
DEFAULT_SCAN_ROOTS = (Path("src"), Path("scripts"), Path("tests"))


def _source_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix == ".py":
            yield root
        return
    for child in sorted(root.rglob("*.py")):
        if set(child.parts) & SKIPPED_DIRS:
            continue
        yield child


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = [node.func.attr]
        value = node.func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
            return ".".join(reversed(parts))
    return None


def _raise_name(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return _call_name(node)
    return None


def _has_keyword(node: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in node.keywords)


def _text_file_call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        method_name = node.func.attr
        if method_name in TEXT_FILE_METHODS:
            return method_name
        if method_name == "open" and _open_call_uses_text_mode(node):
            return "open"
    if isinstance(node.func, ast.Name) and node.func.id == "open":
        if _open_call_uses_text_mode(node, mode_arg_index=1):
            return "open"
    return None


def _open_call_uses_text_mode(node: ast.Call, *, mode_arg_index: int = 0) -> bool:
    call_name = _call_name(node)
    if call_name and call_name.split(".", 1)[0] in ARCHIVE_OPEN_ROOTS:
        return False

    mode = _open_mode(node, mode_arg_index)
    return mode is None or "b" not in mode


def _open_mode(node: ast.Call, mode_arg_index: int) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == "mode":
            return _constant_string(keyword.value)
    if len(node.args) > mode_arg_index:
        return _constant_string(node.args[mode_arg_index])
    return None


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _check_comments(path: Path, text: str) -> list[str]:
    failures: list[str] = []
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        for marker in FORBIDDEN_COMMENT_MARKERS:
            if marker in token.string:
                failures.append(
                    f"{path}:{token.start[0]}: forbidden comment marker {marker!r}"
                )
    return failures


def _allows_cli_print(path: Path) -> bool:
    return bool(set(path.parts) & CLI_SCRIPT_ROOTS)


def _check_ast(path: Path, tree: ast.AST) -> list[str]:
    failures: list[str] = []
    forbidden_calls = set(FORBIDDEN_CALLS)
    if _allows_cli_print(path):
        forbidden_calls.discard("print")
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            failures.append(f"{path}:{node.lineno}: bare except is not allowed")

        if isinstance(node, ast.Import):
            for alias in node.names:
                root_name = alias.name.split(".", 1)[0]
                if root_name in FORBIDDEN_IMPORT_ROOTS:
                    failures.append(
                        f"{path}:{node.lineno}: forbidden debug import {alias.name!r}"
                    )

        if isinstance(node, ast.ImportFrom):
            root_name = (node.module or "").split(".", 1)[0]
            if root_name in FORBIDDEN_IMPORT_ROOTS:
                failures.append(
                    f"{path}:{node.lineno}: forbidden debug import {node.module!r}"
                )

        if isinstance(node, ast.Call):
            call_name = _call_name(node)
            call_root_name = call_name.split(".", 1)[0] if call_name else None
            if call_name in forbidden_calls or call_root_name in FORBIDDEN_IMPORT_ROOTS:
                failures.append(
                    f"{path}:{node.lineno}: forbidden debug call {call_name!r}"
                )
            text_call_name = _text_file_call_name(node)
            if text_call_name and not _has_keyword(node, "encoding"):
                failures.append(
                    f"{path}:{node.lineno}: text I/O must set encoding=: "
                    f"{text_call_name}"
                )

        if isinstance(node, ast.Raise):
            raise_name = _raise_name(node.exc)
            if raise_name in FORBIDDEN_RAISES:
                failures.append(
                    f"{path}:{node.lineno}: use abc.abstractmethod instead of "
                    f"raising {raise_name}"
                )

    return failures


def _check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    failures = _check_comments(path, text)
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]
    failures.extend(_check_ast(path, tree))
    return failures


def _failures(roots: Iterable[Path]) -> list[str]:
    failures: list[str] = []
    for root in roots:
        for path in _source_files(root):
            failures.extend(_check_file(path))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=(
            "Source files or directories to scan. Defaults to src, scripts, and tests."
        ),
    )
    args = parser.parse_args()

    paths = tuple(args.paths) if args.paths else DEFAULT_SCAN_ROOTS
    failures = _failures(paths)
    if failures:
        print("source hygiene check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Source hygiene check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
