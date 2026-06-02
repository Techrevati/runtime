"""Ensure documentation publishing keeps the custom neutral output."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MKDOCS_REQUIRED = (
    "site_name: Runtime",
    "copyright: Copyright 2026 Techrevati doo",
    "theme:\n  name: null\n  custom_dir: docs_theme",
    "hooks:\n  - scripts/mkdocs_hooks/remove_generator_meta.py",
    "extra:\n  generator: false",
)
MKDOCS_FORBIDDEN = (
    "name: material",
    "search:",
    "mkdocs-" + "material",
)
THEME_REQUIRED = (
    '<link rel="stylesheet" href="{{ \'styles/runtime.css\'|url }}">',
    '<footer class="site-footer">',
    "{{ config.copyright }}",
)
WORKFLOW_REQUIRED = (
    "python scripts/install_toolchain.py docs",
    "mkdocs build --strict",
    "python scripts/check_public_branding.py site",
    "if: github.event_name == 'push'",
    "needs: docs",
    "contents: write",
    "publish_dir: ./site",
)
REQUIRED_DOC_PAGES = ("compliance/index.md",)
FORBIDDEN_DOC_SNIPPETS = (
    "AgentSession = " + "Orchestrator",
    "Coming in " + "0.2.0 (forward" + "-looking)",
    "Sig" + "store signing on release artifacts",
    "0.3.0 " + "Sprint 6",
    "Full tool-call-level nesting is still a 0.3.0 " + "item",
)


def _runtime_modules(root: Path) -> list[str]:
    runtime_dir = root / "src" / "techrevati" / "runtime"
    # Private modules (leading underscore, e.g. _internal) are implementation
    # detail — they carry no public API and need no reference page.
    return sorted(
        path.stem
        for path in runtime_dir.glob("*.py")
        if path.name != "__init__.py" and not path.name.startswith("_")
    )


def _pattern_pages(root: Path) -> list[str]:
    patterns_dir = root / "docs" / "patterns"
    return sorted(path.name for path in patterns_dir.glob("*.md"))


def _nav_markdown_pages(root: Path) -> list[str]:
    docs_dir = root / "docs"
    skipped_prefixes = ("api/", "patterns/")
    return sorted(
        path.relative_to(docs_dir).as_posix()
        for path in docs_dir.rglob("*.md")
        if not path.relative_to(docs_dir).as_posix().startswith(skipped_prefixes)
    )


def _check_changelog_mirror(root: Path) -> list[str]:
    changelog_source = root / "CHANGELOG.md"
    changelog_page = root / "docs" / "changelog.md"
    if not changelog_page.exists():
        return ["docs/changelog.md is missing"]
    if changelog_page.is_symlink():
        return ["docs/changelog.md must be a regular file, not a symlink"]
    if changelog_page.read_text(encoding="utf-8") != changelog_source.read_text(
        encoding="utf-8"
    ):
        return ["docs/changelog.md must match CHANGELOG.md"]
    return []


def _check_forbidden_doc_snippets(root: Path) -> list[str]:
    failures: list[str] = []
    docs_root = root / "docs"
    for path in sorted(docs_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_DOC_SNIPPETS:
            if snippet in text:
                relative_path = path.relative_to(root).as_posix()
                failures.append(
                    f"{relative_path} contains stale docs snippet: {snippet}"
                )
    return failures


def _check_docs(root: Path) -> list[str]:
    failures: list[str] = []
    mkdocs_text = (root / "mkdocs.yml").read_text(encoding="utf-8")
    theme_text = (root / "docs_theme" / "main.html").read_text(encoding="utf-8")
    workflow_text = (root / ".github" / "workflows" / "docs.yml").read_text(
        encoding="utf-8"
    )

    for snippet in MKDOCS_REQUIRED:
        if snippet not in mkdocs_text:
            failures.append(f"mkdocs.yml is missing required snippet: {snippet}")

    for snippet in MKDOCS_FORBIDDEN:
        if snippet in mkdocs_text:
            failures.append(f"mkdocs.yml contains forbidden snippet: {snippet}")

    for snippet in THEME_REQUIRED:
        if snippet not in theme_text:
            failures.append(f"docs_theme/main.html is missing snippet: {snippet}")

    if workflow_text.count("mkdocs build --strict") < 2:
        failures.append("docs workflow must build docs in both build and deploy jobs")
    if workflow_text.count("python scripts/check_public_branding.py site") < 2:
        failures.append(
            "docs workflow must run the published HTML branding guard twice"
        )
    for snippet in WORKFLOW_REQUIRED:
        if snippet not in workflow_text:
            failures.append(f"docs workflow is missing required snippet: {snippet}")

    deploy_index = workflow_text.find("Deploy documentation")
    guard_index = workflow_text.rfind(
        "python scripts/check_public_branding.py site",
        0,
        deploy_index if deploy_index != -1 else len(workflow_text),
    )
    if deploy_index == -1 or guard_index == -1 or guard_index > deploy_index:
        failures.append("docs workflow must run branding guard before deploy")

    for module_name in _runtime_modules(root):
        api_doc = root / "docs" / "api" / f"{module_name}.md"
        api_nav = f"api/{module_name}.md"
        api_directive = f"::: techrevati.runtime.{module_name}"
        if not api_doc.exists():
            failures.append(f"missing API reference page for {module_name}")
            continue
        if api_nav not in mkdocs_text:
            failures.append(f"mkdocs.yml is missing API nav entry for {module_name}")
        if api_directive not in api_doc.read_text(encoding="utf-8"):
            failures.append(f"API page for {module_name} is missing mkdocstrings entry")

    for pattern_page in _pattern_pages(root):
        pattern_nav = f"patterns/{pattern_page}"
        if pattern_nav not in mkdocs_text:
            failures.append(
                f"mkdocs.yml is missing Patterns nav entry for {pattern_nav}"
            )

    for docs_page in _nav_markdown_pages(root):
        if docs_page not in mkdocs_text:
            failures.append(
                f"mkdocs.yml is missing nav entry for docs page {docs_page}"
            )

    for docs_page in REQUIRED_DOC_PAGES:
        page_path = root / "docs" / docs_page
        if not page_path.is_file():
            failures.append(f"required docs page is missing: docs/{docs_page}")
        elif docs_page not in mkdocs_text:
            failures.append(
                f"mkdocs.yml is missing nav entry for required docs page {docs_page}"
            )

    failures.extend(_check_forbidden_doc_snippets(root))
    failures.extend(_check_changelog_mirror(root))

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing documentation config.",
    )
    args = parser.parse_args()

    failures = _check_docs(args.root)
    if failures:
        print("docs publication check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Docs publication check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
