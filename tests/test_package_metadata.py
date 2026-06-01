from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any

from techrevati import runtime

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


def _project_metadata() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        return dict(tomllib.load(handle)["project"])


def test_runtime_version_matches_installed_distribution_metadata() -> None:
    project = _project_metadata()
    assert runtime.__version__ == project["version"]
    assert importlib.metadata.version(project["name"]) == project["version"]


def test_public_exports_are_declared_and_resolvable() -> None:
    exports = runtime.__all__
    assert len(exports) == len(set(exports))
    for name in exports:
        assert hasattr(runtime, name), name


def test_distribution_metadata_has_expected_public_identity() -> None:
    project = _project_metadata()
    metadata = importlib.metadata.metadata(project["name"])
    assert metadata["Name"] == project["name"]
    assert metadata["Version"] == project["version"]
    assert metadata["Author"] == "Techrevati doo"
    assert metadata["Summary"] == project["description"]
    assert metadata["License-Expression"] == project["license"]
    assert metadata.get_all("Project-URL") is None
