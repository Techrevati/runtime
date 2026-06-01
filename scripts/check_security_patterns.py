"""Reject high-risk Python patterns before they reach production code."""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Final

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
FORBIDDEN_IMPORT_ROOTS = {"marshal", "pickle", "shelve"}
FORBIDDEN_CALLS = {
    "eval",
    "exec",
    "logging.exception",
    "logger.exception",
    "os.system",
    "ssl._create_unverified_context",
    "subprocess.Popen",
    "tempfile.mktemp",
    "yaml.full_load",
    "yaml.full_load_all",
    "yaml.unsafe_load",
    "yaml.unsafe_load_all",
}
SHELLABLE_SUBPROCESS_CALLS = {
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "subprocess.run",
}
SUBPROCESS_CALLS_REQUIRING_TIMEOUT = {
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
}
SUBPROCESS_RUN_CALLS_REQUIRING_CHECK = {"subprocess.run"}
HTTP_CLIENT_CALLS_REQUIRING_TIMEOUT = {
    "httpx.delete",
    "httpx.get",
    "httpx.head",
    "httpx.options",
    "httpx.patch",
    "httpx.post",
    "httpx.put",
    "httpx.request",
    "requests.delete",
    "requests.get",
    "requests.head",
    "requests.options",
    "requests.patch",
    "requests.post",
    "requests.put",
    "requests.request",
    "urllib.request.urlopen",
}
HTTP_METHOD_NAMES = {
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "request",
    "urlopen",
}
SAFE_YAML_LOADERS = {"CSafeLoader", "SafeLoader"}
YAML_LOAD_CALLS = {"yaml.load", "yaml.load_all"}
RAW_EXCEPTION_NAMES = {"exc", "err", "error"}
LOGGING_CALL_ROOTS = {"logging", "logger"}
LOGGING_METHOD_NAMES = {
    "critical",
    "debug",
    "error",
    "info",
    "log",
    "warning",
}
OBSERVABILITY_PAYLOAD_CALLS = {
    "AgentEvent.failed",
    "StreamEvent.error",
    "StreamEvent.final",
}
OBSERVABILITY_PAYLOAD_METHODS = {"fail", "with_detail"}
DEFAULT_SCAN_ROOTS = (Path("src"), Path("scripts"), Path("tests"))
_MISSING: Final[object] = object()


def _source_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            if path.suffix == ".py":
                yield path
            continue
        if not path.is_dir():
            continue
        for child in sorted(path.rglob("*.py")):
            if set(child.parts) & SKIPPED_DIRS:
                continue
            yield child


def _keyword_bool(node: ast.Call, name: str, expected: bool) -> bool:
    value = _keyword_constant(node, name)
    if expected:
        return value is not _MISSING and bool(value) is True
    return value is False or (type(value) is int and value == 0)


def _keyword_constant(node: ast.Call, name: str) -> object:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return _MISSING


def _has_keyword(node: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in node.keywords)


def _loader_is_safe(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in SAFE_YAML_LOADERS
    if isinstance(node, ast.Attribute):
        return node.attr in SAFE_YAML_LOADERS
    return False


class SecurityPatternVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.failures: list[str] = []
        self.aliases: dict[str, str] = {}

    def _failure(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", 1)
        self.failures.append(f"{self.path}:{line}: {message}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_name = alias.name.split(".", 1)[0]
            local_name = alias.asname or root_name
            self.aliases[local_name] = root_name
            if root_name in FORBIDDEN_IMPORT_ROOTS:
                self._failure(node, f"forbidden unsafe import {alias.name!r}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = node.module or ""
        root_name = module_name.split(".", 1)[0]
        if root_name in FORBIDDEN_IMPORT_ROOTS:
            self._failure(node, f"forbidden unsafe import {module_name!r}")
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.aliases[local_name] = f"{module_name}.{alias.name}"
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = self._call_name(node)
        if call_name in FORBIDDEN_CALLS:
            self._failure(node, f"forbidden high-risk call {call_name!r}")
        if call_name in SHELLABLE_SUBPROCESS_CALLS and _keyword_bool(
            node, "shell", True
        ):
            self._failure(node, f"subprocess call must not use shell=True: {call_name}")
        if call_name in SUBPROCESS_CALLS_REQUIRING_TIMEOUT and not _has_keyword(
            node, "timeout"
        ):
            self._failure(node, f"subprocess call must set timeout=: {call_name}")
        if call_name in SUBPROCESS_RUN_CALLS_REQUIRING_CHECK and not _has_keyword(
            node, "check"
        ):
            self._failure(
                node,
                f"subprocess.run must set check= explicitly: {call_name}",
            )
        if self._is_http_client_call(call_name, node) and not _has_keyword(
            node, "timeout"
        ):
            target = call_name or "HTTP client call"
            self._failure(node, f"HTTP client call must set timeout=: {target}")
        if call_name in YAML_LOAD_CALLS and not self._yaml_loader_is_safe(node):
            self._failure(
                node,
                f"{call_name} must use SafeLoader or CSafeLoader",
            )
        if _keyword_bool(node, "verify", False):
            target = call_name or "call"
            self._failure(node, f"TLS verification must not be disabled: {target}")
        if _keyword_bool(node, "exc_info", True) or _keyword_bool(
            node, "stack_info", True
        ):
            target = call_name or "call"
            self._failure(
                node,
                "logging must not include raw exception or stack traces; "
                f"use a sanitized diagnostic: {target}",
            )
        if call_name and self._is_logging_call(call_name):
            if self._contains_raw_exception_text(node):
                self._failure(
                    node,
                    "logging must not copy raw exception text; "
                    "use a sanitized diagnostic",
                )
        if call_name and self._is_observability_payload_call(call_name):
            if self._contains_raw_exception_text(node):
                self._failure(
                    node,
                    "observability payload must not copy raw exception text; "
                    "use a sanitized diagnostic",
                )
        self.generic_visit(node)

    def _call_name(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return self.aliases.get(node.func.id, node.func.id)
        if not isinstance(node.func, ast.Attribute):
            return None

        parts: list[str] = [node.func.attr]
        value = node.func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if not isinstance(value, ast.Name):
            return None

        root_name = self.aliases.get(value.id, value.id)
        expanded = [*root_name.split("."), *reversed(parts)]
        return ".".join(expanded)

    def _yaml_loader_is_safe(self, node: ast.Call) -> bool:
        for keyword in node.keywords:
            if keyword.arg == "Loader":
                return _loader_is_safe(keyword.value)
        return False

    def _is_observability_payload_call(self, call_name: str) -> bool:
        if any(
            call_name == expected or call_name.endswith(f".{expected}")
            for expected in OBSERVABILITY_PAYLOAD_CALLS
        ):
            return True
        method_name = call_name.rsplit(".", 1)[-1]
        return method_name in OBSERVABILITY_PAYLOAD_METHODS

    def _is_logging_call(self, call_name: str) -> bool:
        parts = call_name.split(".")
        method_name = parts[-1]
        if method_name not in LOGGING_METHOD_NAMES:
            return False
        return any(part in LOGGING_CALL_ROOTS for part in parts[:-1])

    def _is_http_client_call(self, call_name: str | None, node: ast.Call) -> bool:
        if call_name in HTTP_CLIENT_CALLS_REQUIRING_TIMEOUT:
            return True
        if not call_name:
            return False
        method_name = call_name.rsplit(".", 1)[-1]
        if method_name not in HTTP_METHOD_NAMES:
            return False
        return bool(
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith(("http://", "https://"))
        )

    def _contains_raw_exception_text(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if self._is_raw_exception_string(child) or self._is_raw_exception_format(
                child
            ):
                return True
        return False

    def _is_raw_exception_string(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if self._call_name(node) != "str":
            return False
        return bool(
            node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in RAW_EXCEPTION_NAMES
        )

    def _is_raw_exception_format(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.FormattedValue):
            return False
        return isinstance(node.value, ast.Name) and node.value.id in RAW_EXCEPTION_NAMES


def _check_code(path: Path, text: str) -> list[str]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]

    visitor = SecurityPatternVisitor(path)
    visitor.visit(tree)
    return visitor.failures


def _failures(paths: Iterable[Path]) -> list[str]:
    failures: list[str] = []
    for path in _source_files(paths):
        failures.extend(_check_code(path, path.read_text(encoding="utf-8")))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help=(
            "Python files or directories to scan. Defaults to src, scripts, and tests."
        ),
    )
    args = parser.parse_args()

    paths = tuple(args.paths) if args.paths else DEFAULT_SCAN_ROOTS
    failures = _failures(paths)
    if failures:
        print("security pattern check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Security pattern check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
