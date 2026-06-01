from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_security_patterns_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_security_patterns.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_security_patterns", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_security_patterns_accepts_clean_code() -> None:
    module = _load_security_patterns_module()
    code = """
from pathlib import Path

def load(path: Path) -> str:
    return path.read_text(encoding="utf-8")
"""

    assert module._check_code(Path("clean.py"), code) == []


def test_security_patterns_rejects_dynamic_execution() -> None:
    module = _load_security_patterns_module()
    code = """
def run(payload: str) -> None:
    eval(payload)
    exec(payload)
"""

    failures = module._check_code(Path("dynamic.py"), code)

    assert sum("high-risk call" in failure for failure in failures) == 2


def test_security_patterns_rejects_unsafe_serialization_imports() -> None:
    module = _load_security_patterns_module()
    code = """
import pickle
from marshal import loads
"""

    failures = module._check_code(Path("serialization.py"), code)

    assert any("pickle" in failure for failure in failures)
    assert any("marshal" in failure for failure in failures)


def test_security_patterns_rejects_shell_subprocess_and_os_system() -> None:
    module = _load_security_patterns_module()
    code = """
import os
import subprocess as sp
from subprocess import check_output

sp.run("echo ok", shell=True)
sp.Popen("echo ok", shell=1)
sp.call("echo ok", shell="yes")
check_output("echo ok", shell=True)
os.system("echo ok")
"""

    failures = module._check_code(Path("shell.py"), code)

    assert sum("shell=True" in failure for failure in failures) == 4
    assert any("os.system" in failure for failure in failures)
    assert any("subprocess.Popen" in failure for failure in failures)


def test_security_patterns_requires_subprocess_timeout() -> None:
    module = _load_security_patterns_module()
    code = """
import subprocess
from subprocess import check_call

subprocess.run(["echo", "ok"])
subprocess.check_output(["echo", "ok"], timeout=15)
check_call(["echo", "ok"])
"""

    failures = module._check_code(Path("timeout.py"), code)

    assert sum("must set timeout" in failure for failure in failures) == 2


def test_security_patterns_requires_subprocess_run_check() -> None:
    module = _load_security_patterns_module()
    code = """
import subprocess

subprocess.run(["echo", "ok"], timeout=15)
subprocess.run(["echo", "ok"], check=False, timeout=15)
"""

    failures = module._check_code(Path("subprocess_check.py"), code)

    assert sum("must set check= explicitly" in failure for failure in failures) == 1


def test_security_patterns_requires_http_client_timeout() -> None:
    module = _load_security_patterns_module()
    code = """
import httpx
import requests as rq
import urllib.request
from requests import post
from urllib.request import urlopen

httpx.get("https://example.invalid")
rq.request("GET", "https://example.invalid")
post("https://example.invalid", timeout=15)
urllib.request.urlopen("https://example.invalid")
urlopen("https://example.invalid", timeout=15)
client.get("https://example.invalid")
"""

    failures = module._check_code(Path("http_timeout.py"), code)

    assert (
        sum("HTTP client call must set timeout" in failure for failure in failures) == 4
    )


def test_security_patterns_rejects_logger_exception() -> None:
    module = _load_security_patterns_module()
    code = """
import logging

logger = logging.getLogger("runtime")

try:
    run()
except Exception:
    logger.exception("failed")
    logging.exception("also failed")
"""

    failures = module._check_code(Path("logging.py"), code)

    assert sum("logger.exception" in failure for failure in failures) == 1
    assert sum("logging.exception" in failure for failure in failures) == 1


def test_security_patterns_rejects_raw_logging_tracebacks() -> None:
    module = _load_security_patterns_module()
    code = """
import logging

logger = logging.getLogger("runtime")

def record() -> None:
    logger.error("failed", exc_info=True)
    logging.warning("stack", stack_info=True)
"""

    failures = module._check_code(Path("raw_logging.py"), code)

    assert sum("raw exception or stack traces" in failure for failure in failures) == 2


def test_security_patterns_rejects_raw_exception_logging_text() -> None:
    module = _load_security_patterns_module()
    code = """
import logging

logger = logging.getLogger("runtime")

def record(exc: Exception, error: Exception) -> None:
    logger.error("failed: %s", str(exc))
    logging.warning(f"failed: {error}")
    logger.info("failed: %s", type(error).__name__)
"""

    failures = module._check_code(Path("raw_logging_text.py"), code)

    assert (
        sum(
            "logging must not copy raw exception text" in failure
            for failure in failures
        )
        == 2
    )


def test_security_patterns_rejects_unsafe_temp_ssl_yaml_and_tls() -> None:
    module = _load_security_patterns_module()
    code = """
import httpx
import ssl
import tempfile
import yaml
from requests import get

tempfile.mktemp()
ssl._create_unverified_context()
yaml.load("value: 1")
httpx.get("https://example.invalid", verify=False)
get("https://example.invalid", verify=False)
"""

    failures = module._check_code(Path("unsafe.py"), code)

    assert any("tempfile.mktemp" in failure for failure in failures)
    assert any("ssl._create_unverified_context" in failure for failure in failures)
    assert any("yaml.load" in failure for failure in failures)
    assert sum("TLS verification" in failure for failure in failures) == 2
    assert (
        sum("HTTP client call must set timeout" in failure for failure in failures) == 2
    )


def test_security_patterns_rejects_session_tls_bypass_and_unsafe_yaml_helpers() -> None:
    module = _load_security_patterns_module()
    code = """
import yaml

def load(client):
    yaml.load_all("value: 1")
    yaml.full_load("value: 1")
    yaml.unsafe_load("!!python/object/apply:os.system ['echo nope']")
    client.get("https://example.invalid", verify=False)
    client.post("https://example.invalid", verify=0)
"""

    failures = module._check_code(Path("more_unsafe.py"), code)

    assert any("yaml.load_all" in failure for failure in failures)
    assert any("yaml.full_load" in failure for failure in failures)
    assert any("yaml.unsafe_load" in failure for failure in failures)
    assert sum("TLS verification" in failure for failure in failures) == 2
    assert (
        sum("HTTP client call must set timeout" in failure for failure in failures) == 2
    )


def test_security_patterns_allows_safe_yaml_loader() -> None:
    module = _load_security_patterns_module()
    code = """
import yaml

yaml.load("value: 1", Loader=yaml.SafeLoader)
yaml.load_all("value: 1", Loader=yaml.CSafeLoader)
"""

    assert module._check_code(Path("yaml_safe.py"), code) == []


def test_security_patterns_rejects_raw_exception_observability_payloads() -> None:
    module = _load_security_patterns_module()
    code = """
from techrevati.runtime import AgentEvent, AgentFailureClass, StreamEvent

def record(exc: Exception, session):
    AgentEvent.failed("r", "p", AgentFailureClass.LLM_ERROR, detail=str(exc))
    StreamEvent.error(type(exc).__name__, str(exc))
    StreamEvent.final("failed", detail=str(exc))
    session.fail(detail=str(exc), failure_class=AgentFailureClass.LLM_ERROR)
"""

    failures = module._check_code(Path("observability.py"), code)

    assert len(failures) == 4
    assert all("raw exception text" in failure for failure in failures)


def test_security_patterns_allows_sanitized_exception_observability_payloads() -> None:
    module = _load_security_patterns_module()
    code = """
from techrevati.runtime import AgentEvent, AgentFailureClass, StreamEvent

def safe_exception_detail(exc: Exception) -> str:
    return f"{type(exc).__name__} raised"

def record(exc: Exception, session):
    detail = safe_exception_detail(exc)
    AgentEvent.failed("r", "p", AgentFailureClass.LLM_ERROR, detail=detail)
    StreamEvent.error(type(exc).__name__, detail)
    StreamEvent.final("failed", detail=detail)
    session.fail(detail=detail, failure_class=AgentFailureClass.LLM_ERROR)
"""

    assert module._check_code(Path("observability.py"), code) == []


def test_security_patterns_allows_internal_exception_classification() -> None:
    module = _load_security_patterns_module()
    code = """
def classify(error: Exception) -> bool:
    error_text = str(error).lower()
    return "timeout" in error_text
"""

    assert module._check_code(Path("classify.py"), code) == []


def test_security_patterns_source_files_skip_cache_dirs(tmp_path: Path) -> None:
    module = _load_security_patterns_module()
    source = tmp_path / "src"
    cache = source / "__pycache__"
    cache.mkdir(parents=True)
    clean = source / "clean.py"
    cached = cache / "cached.py"
    clean.write_text("VALUE = 1\n", encoding="utf-8")
    cached.write_text("eval('1')\n", encoding="utf-8")

    files = list(module._source_files([source]))

    assert files == [clean]


def test_security_patterns_default_scan_roots_include_tests() -> None:
    module = _load_security_patterns_module()

    assert module.DEFAULT_SCAN_ROOTS == (
        Path("src"),
        Path("scripts"),
        Path("tests"),
    )
