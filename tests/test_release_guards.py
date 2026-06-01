from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType


def _load_release_tag_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_release_tag.py"
    )
    spec = importlib.util.spec_from_file_location("check_release_tag", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_changelog_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "check_changelog.py"
    spec = importlib.util.spec_from_file_location("check_changelog", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_tag_accepts_matching_releasable_version() -> None:
    module = _load_release_tag_module()
    assert module._check_release("1.2.3", "v1.2.3") is None
    assert module._check_release("1.2.3rc1", "v1.2.3rc1") is None
    assert module._check_release("1.2.3.post1", "v1.2.3.post1") is None


def test_release_tag_rejects_mismatched_tag() -> None:
    module = _load_release_tag_module()
    error = module._check_release("1.2.3", "v1.2.4")
    assert error is not None
    assert "does not match" in error


def test_release_tag_rejects_missing_tag() -> None:
    module = _load_release_tag_module()
    error = module._check_release("1.2.3", None)
    assert error is not None
    assert "missing" in error


def test_release_tag_rejects_non_releasable_versions() -> None:
    module = _load_release_tag_module()
    for version in (
        "1.2.3.dev1",
        "1.2.3a1",
        "1.2.3b1",
        "1.2.3+local",
    ):
        error = module._check_release(version, f"v{version}")
        assert error is not None
        assert "not a releasable version" in error


def test_changelog_accepts_documented_version() -> None:
    module = _load_changelog_module()
    changelog = """
# Changelog

## 1.2.3 - 2026-05-30

Fixed:

- Corrected release packaging.
"""
    assert module._check_changelog("1.2.3", changelog) == []


def test_changelog_accepts_bracketed_version_heading() -> None:
    module = _load_changelog_module()
    changelog = """
# Changelog

## [1.2.3] - 2026-05-30

- Corrected release packaging.
"""
    assert module._check_changelog("1.2.3", changelog) == []


def test_changelog_rejects_missing_version() -> None:
    module = _load_changelog_module()
    failures = module._check_changelog("1.2.3", "# Changelog\n")
    assert failures
    assert "missing" in failures[0]


def test_changelog_rejects_duplicate_version() -> None:
    module = _load_changelog_module()
    changelog = """
# Changelog

## 1.2.3 - 2026-05-30

- First entry.

## 1.2.3 - 2026-05-31

- Duplicate entry.
"""
    failures = module._check_changelog("1.2.3", changelog)
    assert any("duplicate" in failure for failure in failures)


def test_changelog_rejects_current_version_below_older_entry() -> None:
    module = _load_changelog_module()
    changelog = """
# Changelog

## 1.2.2 - 2026-05-30

- Older entry.

## 1.2.3 - 2026-05-30

- Current entry.
"""
    failures = module._check_changelog("1.2.3", changelog)

    assert any("must be first" in failure for failure in failures)


def test_changelog_rejects_future_release_date() -> None:
    module = _load_changelog_module()
    changelog = """
# Changelog

## 1.2.3 - 2026-06-01

- Current entry.
"""
    failures = module._check_changelog(
        "1.2.3",
        changelog,
        today=date(2026, 5, 31),
    )

    assert any("future release date" in failure for failure in failures)


def test_changelog_rejects_placeholder_or_empty_body() -> None:
    module = _load_changelog_module()
    placeholder = """
# Changelog

## 1.2.3 - 2026-05-30

TBD
"""
    empty = "# Changelog\n\n## 1.2.3 - 2026-05-30\n"

    assert any(
        "placeholder" in failure
        for failure in module._check_changelog("1.2.3", placeholder)
    )
    assert any(
        "no body" in failure for failure in module._check_changelog("1.2.3", empty)
    )
