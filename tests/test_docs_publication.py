from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_docs_publication_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_docs_publication.py"
    )
    spec = importlib.util.spec_from_file_location("check_docs_publication", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_docs_fixture(root: Path, *, mkdocs_extra: str = "") -> None:
    (root / "docs_theme").mkdir()
    (root / "docs" / "api").mkdir(parents=True)
    (root / "docs" / "compliance").mkdir(parents=True)
    (root / "docs" / "patterns").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "src" / "techrevati" / "runtime").mkdir(parents=True)
    for module_name in ("orchestrator", "routing"):
        (root / "src" / "techrevati" / "runtime" / f"{module_name}.py").write_text(
            "",
            encoding="utf-8",
        )
        (root / "docs" / "api" / f"{module_name}.md").write_text(
            "\n".join(
                (f"# {module_name}", "", f"::: techrevati.runtime.{module_name}")
            ),
            encoding="utf-8",
        )
    for pattern_name in ("orchestrator", "routing"):
        (root / "docs" / "patterns" / f"{pattern_name}.md").write_text(
            f"# {pattern_name}\n",
            encoding="utf-8",
        )
    (root / "docs" / "getting-started.md").write_text(
        "# Getting Started\n",
        encoding="utf-8",
    )
    (root / "docs" / "tutorials").mkdir()
    (root / "docs" / "tutorials" / "end-to-end.md").write_text(
        "# End-to-End Tutorial\n",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "index.md").write_text(
        "# Compliance Mapping\n",
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    (root / "docs" / "changelog.md").write_text("# Changelog\n", encoding="utf-8")
    (root / "mkdocs.yml").write_text(
        "\n".join(
            (
                "site_name: Runtime",
                "copyright: Copyright 2026 Techrevati doo",
                "",
                "theme:",
                "  name: null",
                "  custom_dir: docs_theme",
                "",
                "hooks:",
                "  - scripts/mkdocs_hooks/remove_generator_meta.py",
                "",
                "extra:",
                "  generator: false",
                "",
                "nav:",
                "  - Getting Started: getting-started.md",
                "  - End-to-End Tutorial: tutorials/end-to-end.md",
                "  - Compliance Mapping: compliance/index.md",
                "  - Changelog: changelog.md",
                "  - Patterns:",
                "    - Orchestrator: patterns/orchestrator.md",
                "    - Routing: patterns/routing.md",
                "  - API Reference:",
                "    - Orchestrator: api/orchestrator.md",
                "    - Routing: api/routing.md",
                mkdocs_extra,
            )
        ),
        encoding="utf-8",
    )
    (root / "docs_theme" / "main.html").write_text(
        "\n".join(
            (
                '<link rel="stylesheet" href="{{ \'styles/runtime.css\'|url }}">',
                '<footer class="site-footer">',
                "{{ config.copyright }}",
            )
        ),
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "docs.yml").write_text(
        "\n".join(
            (
                "jobs:",
                "  docs:",
                "    steps:",
                "      - run: python scripts/install_toolchain.py docs",
                "      - run: mkdocs build --strict",
                "      - run: python scripts/check_public_branding.py site",
                "  deploy:",
                "    if: github.event_name == 'push'",
                "    needs: docs",
                "    permissions:",
                "      contents: write",
                "    steps:",
                "      - run: python scripts/install_toolchain.py docs",
                "      - run: mkdocs build --strict",
                "      - run: python scripts/check_public_branding.py site",
                "      - name: Deploy documentation",
                "        with:",
                "          publish_dir: ./site",
            )
        ),
        encoding="utf-8",
    )


def test_docs_publication_accepts_expected_config(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)

    assert module._check_docs(tmp_path) == []


def test_docs_publication_rejects_forbidden_theme(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path, mkdocs_extra="name: material")

    failures = module._check_docs(tmp_path)
    assert any("forbidden" in failure for failure in failures)


def test_docs_publication_rejects_missing_theme_contract(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "docs_theme" / "main.html").write_text("", encoding="utf-8")

    failures = module._check_docs(tmp_path)
    assert any("docs_theme/main.html" in failure for failure in failures)


def test_docs_publication_rejects_deploy_without_branding_guard(
    tmp_path: Path,
) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    docs_workflow = tmp_path / ".github" / "workflows" / "docs.yml"
    docs_workflow.write_text(
        docs_workflow.read_text(encoding="utf-8").replace(
            "      - run: python scripts/check_public_branding.py site\n"
            "      - name: Deploy documentation",
            "      - name: Deploy documentation",
        ),
        encoding="utf-8",
    )

    failures = module._check_docs(tmp_path)
    assert any("branding guard" in failure for failure in failures)


def test_docs_publication_rejects_missing_api_page(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "docs" / "api" / "routing.md").unlink()

    failures = module._check_docs(tmp_path)
    assert any(
        "missing API reference page for routing" in failure for failure in failures
    )


def test_docs_publication_rejects_missing_api_nav_entry(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    mkdocs_file = tmp_path / "mkdocs.yml"
    mkdocs_file.write_text(
        mkdocs_file.read_text(encoding="utf-8").replace(
            "    - Routing: api/routing.md\n",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._check_docs(tmp_path)
    assert any("API nav entry for routing" in failure for failure in failures)


def test_docs_publication_rejects_missing_api_directive(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "docs" / "api" / "routing.md").write_text(
        "# Routing\n",
        encoding="utf-8",
    )

    failures = module._check_docs(tmp_path)
    assert any("API page for routing" in failure for failure in failures)


def test_docs_publication_rejects_orphan_pattern_page(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    mkdocs_file = tmp_path / "mkdocs.yml"
    mkdocs_file.write_text(
        mkdocs_file.read_text(encoding="utf-8").replace(
            "    - Routing: patterns/routing.md\n",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._check_docs(tmp_path)
    assert any("Patterns nav entry" in failure for failure in failures)


def test_docs_publication_rejects_orphan_docs_page(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    mkdocs_file = tmp_path / "mkdocs.yml"
    mkdocs_file.write_text(
        mkdocs_file.read_text(encoding="utf-8").replace(
            "  - End-to-End Tutorial: tutorials/end-to-end.md\n",
            "",
        ),
        encoding="utf-8",
    )

    failures = module._check_docs(tmp_path)
    assert any("docs page tutorials/end-to-end.md" in failure for failure in failures)


def test_docs_publication_rejects_missing_required_compliance_page(
    tmp_path: Path,
) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "index.md").unlink()

    failures = module._check_docs(tmp_path)
    assert any("docs/compliance/index.md" in failure for failure in failures)


def test_docs_publication_rejects_stale_docs_snippet(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "docs" / "getting-started.md").write_text(
        "# Getting Started\n\nAgentSession = " + "Orchestrator\n",
        encoding="utf-8",
    )

    failures = module._check_docs(tmp_path)
    assert any("stale docs snippet" in failure for failure in failures)


def test_docs_publication_rejects_stale_changelog_mirror(tmp_path: Path) -> None:
    module = _load_docs_publication_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "docs" / "changelog.md").write_text(
        "# Older Changelog\n",
        encoding="utf-8",
    )

    failures = module._check_docs(tmp_path)
    assert any(
        "docs/changelog.md must match CHANGELOG.md" in failure for failure in failures
    )
