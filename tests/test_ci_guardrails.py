from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_ci_guardrails_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_ci_guardrails.py"
    )
    spec = importlib.util.spec_from_file_location("check_ci_guardrails", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_job_text() -> str:
    module = _load_ci_guardrails_module()
    lines = [
        "run: python scripts/install_toolchain.py audit",
        *(f"run: python scripts/{guard}" for guard in module.VERIFY_GUARDS),
    ]
    lines.append("run: python scripts/check_module_coverage.py --threshold 85")
    return "\n".join(lines)


def test_ci_guardrails_accepts_verify_job_with_all_guards() -> None:
    module = _load_ci_guardrails_module()
    assert module._check_verify_job("ci.yml", _verify_job_text()) == []


def test_ci_guardrails_rejects_missing_verify_guard() -> None:
    module = _load_ci_guardrails_module()
    job = _verify_job_text().replace(
        "run: python scripts/check_package_policy.py\n",
        "",
    )

    failures = module._check_verify_job("ci.yml", job)
    assert any("check_package_policy.py" in failure for failure in failures)


def test_ci_guardrails_rejects_missing_coverage_guard() -> None:
    module = _load_ci_guardrails_module()
    job = _verify_job_text().replace(
        "run: python scripts/check_module_coverage.py --threshold 85",
        "",
    )

    failures = module._check_verify_job("ci.yml", job)
    assert any("coverage" in failure for failure in failures)


def test_ci_guardrails_rejects_missing_audit_toolchain_install() -> None:
    module = _load_ci_guardrails_module()
    job = _verify_job_text().replace(
        "run: python scripts/install_toolchain.py audit\n",
        "",
    )

    failures = module._check_verify_job("ci.yml", job)
    assert any("audit toolchain" in failure for failure in failures)


def test_ci_guardrails_checks_required_snippets() -> None:
    module = _load_ci_guardrails_module()
    failures = module._check_required_snippets(
        "ci.yml",
        "python -m twine check dist/*.whl dist/*.tar.gz",
        (
            "python -m twine check dist/*.whl dist/*.tar.gz",
            "python scripts/check_distribution.py dist",
        ),
    )

    assert failures == [
        "ci.yml: missing required snippet python scripts/check_distribution.py dist"
    ]


def test_ci_guardrails_require_dependency_free_local_installs() -> None:
    module = _load_ci_guardrails_module()
    snippets = (*module.CI_BUILD_SNIPPETS, *module.RELEASE_SNIPPETS)
    install_snippets = [snippet for snippet in snippets if "pip install" in snippet]

    assert install_snippets
    assert all("--no-deps" in snippet for snippet in install_snippets)
    assert all("--no-index" in snippet for snippet in install_snippets)


def test_ci_guardrails_rejects_unclassified_guard_script(tmp_path: Path) -> None:
    module = _load_ci_guardrails_module()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in module.VERIFY_GUARDS:
        (scripts / name).write_text("", encoding="utf-8")
    for name in module.SPECIAL_CHECKS:
        (scripts / name).write_text("", encoding="utf-8")
    (scripts / "check_new_guard.py").write_text("", encoding="utf-8")

    failures = module._check_guard_inventory(tmp_path)
    assert any("check_new_guard.py" in failure for failure in failures)
