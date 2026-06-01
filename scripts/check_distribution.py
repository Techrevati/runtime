"""Validate built distribution artifacts before release.

The check is intentionally stdlib-only so it can run in CI immediately after
``python -m build``. It catches the boring-but-expensive mistakes: missing typed
package markers, missing bundled data, accidental runtime dependencies, stale
metadata, and dirty bytecode in wheels or source archives.
"""

from __future__ import annotations

import argparse
import base64
import csv
import email.message
import email.parser
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]


REQUIRED_WHEEL_FILES = {
    "techrevati/runtime/py.typed",
    "techrevati/runtime/data/pricing.json",
}

BASE_REQUIRED_SDIST_SUFFIXES = {
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "docs/styles/runtime.css",
    "docs_theme/404.html",
    "docs_theme/main.html",
    "scripts/install_toolchain.py",
    "scripts/mkdocs_hooks/remove_generator_meta.py",
    "src/techrevati/runtime/py.typed",
    "src/techrevati/runtime/data/pricing.json",
}

BINARY_SUFFIXES = {
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".png",
    ".pyc",
    ".pyo",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}

FORBIDDEN_SDIST_PARTS = {
    ".coverage",
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "build",
    "dist",
    "htmlcov",
    "site",
}
SOURCE_PACKAGE_SKIP_PARTS = {"__pycache__"}


def _term(*parts: str) -> str:
    return "".join(parts)


BANNED_PUBLIC_TERMS = (
    _term("Anth", "ropic"),
    _term("Ar", "min"),
    _term("Code", "cov"),
    _term("Documentation ", "built with"),
    _term("Boot", "strap"),
    _term("Font ", "Awesome"),
    _term("Git", "Hub"),
    _term("Lang", "Graph"),
    _term("Made with ", "Material"),
    _term("Material ", "for ", "MkDocs"),
    _term("Mat", "effy"),
    _term("mkdocs", "-", "material"),
    _term("Open", "AI"),
    _term("Pyd", "antic"),
    _term("Py", "PI"),
    _term("squid", "funk"),
    _term("Temp", "oral"),
)


def _load_project_metadata(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    return dict(pyproject["project"])


def _fail(message: str) -> int:
    print(f"distribution check failed: {message}", file=sys.stderr)
    return 1


def _find_single(dist: Path, pattern: str) -> Path | None:
    matches = sorted(dist.glob(pattern))
    if len(matches) != 1:
        return None
    return matches[0]


def _distribution_prefix(project: dict[str, Any]) -> str:
    return re.sub(r"[-_.]+", "_", project["name"]).lower()


def _expected_wheel_name(project: dict[str, Any]) -> str:
    return f"{_distribution_prefix(project)}-{project['version']}-py3-none-any.whl"


def _expected_sdist_name(project: dict[str, Any]) -> str:
    return f"{_distribution_prefix(project)}-{project['version']}.tar.gz"


def _expected_dist_info_dir(project: dict[str, Any]) -> str:
    return f"{_distribution_prefix(project)}-{project['version']}.dist-info"


def _check_artifact_names(
    wheel: Path, sdist: Path, project: dict[str, Any]
) -> str | None:
    expected_wheel = _expected_wheel_name(project)
    if wheel.name != expected_wheel:
        return f"wheel filename {wheel.name!r} != {expected_wheel!r}"

    expected_sdist = _expected_sdist_name(project)
    if sdist.name != expected_sdist:
        return f"sdist filename {sdist.name!r} != {expected_sdist!r}"

    return None


def _check_wheel_surface(names: set[str], project: dict[str, Any]) -> str | None:
    dist_info_prefix = f"{_expected_dist_info_dir(project)}/"
    for name in sorted(names):
        if name.endswith("/"):
            continue
        if name.startswith("techrevati/") or name.startswith(dist_info_prefix):
            continue
        return f"wheel contains unexpected top-level file: {name}"
    return None


def _has_suffix(paths: set[str], suffix: str) -> bool:
    return any(path.endswith(suffix) for path in paths)


def _match_suffix(paths: set[str], suffix: str) -> str | None:
    matches = sorted(path for path in paths if path.endswith(suffix))
    if len(matches) != 1:
        return None
    return matches[0]


def _required_sdist_suffixes(root: Path) -> set[str]:
    suffixes = set(BASE_REQUIRED_SDIST_SUFFIXES)
    suffixes.update(_documentation_files(root))
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        suffixes.update(
            path.relative_to(root).as_posix()
            for path in scripts_dir.glob("check_*.py")
            if path.is_file()
        )
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        suffixes.update(
            path.relative_to(root).as_posix()
            for path in tests_dir.glob("test_*.py")
            if path.is_file()
        )
    examples_dir = root / "examples"
    if examples_dir.is_dir():
        suffixes.update(
            path.relative_to(root).as_posix()
            for path in examples_dir.iterdir()
            if path.is_file() and path.suffix.lower() not in BINARY_SUFFIXES
        )
    return suffixes


def _documentation_files(root: Path) -> set[str]:
    files = {"mkdocs.yml"} if (root / "mkdocs.yml").is_file() else set()
    for dirname in ("docs", "docs_theme"):
        directory = root / dirname
        if not directory.is_dir():
            continue
        files.update(
            path.relative_to(root).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
        )
    return files


def _source_package_file_contents(root: Path) -> dict[str, bytes]:
    src_dir = root / "src"
    package_dir = src_dir / "techrevati"
    return {
        path.relative_to(src_dir).as_posix(): path.read_bytes()
        for path in package_dir.rglob("*")
        if path.is_file()
        and not (set(path.parts) & SOURCE_PACKAGE_SKIP_PARTS)
        and path.suffix.lower() not in {".pyc", ".pyo"}
    }


def _source_package_files(root: Path) -> set[str]:
    return set(_source_package_file_contents(root))


def _scan_text(name: str, data: bytes) -> str | None:
    if Path(name).suffix.lower() in BINARY_SUFFIXES:
        return None

    text = data.decode("utf-8", errors="replace")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for term in BANNED_PUBLIC_TERMS:
            if term in line:
                return (
                    f"blocked public term {term!r} in {name}:{line_number}: "
                    f"{line.strip()[:160]}"
                )
    return None


def _scan_wheel_public_text(archive: zipfile.ZipFile) -> str | None:
    for name in archive.namelist():
        if name.endswith("/") or name.endswith(".dist-info/RECORD"):
            continue
        if error := _scan_text(name, archive.read(name)):
            return error
    return None


def _normalize_requirement(requirement: str) -> str:
    normalized = requirement.replace(" ", "")
    match = re.fullmatch(
        r"(?P<name>[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?)(?P<spec>.*)",
        normalized,
    )
    if match is None:
        return normalized
    spec = match.group("spec")
    if "," not in spec:
        return normalized
    return f"{match.group('name')}{','.join(sorted(spec.split(',')))}"


def _check_optional_dependency_metadata(
    metadata: email.message.Message, project: dict[str, Any], artifact: str
) -> str | None:
    expected = {
        extra: {_normalize_requirement(requirement) for requirement in requirements}
        for extra, requirements in project.get("optional-dependencies", {}).items()
    }
    provided = set(metadata.get_all("Provides-Extra", []))
    if provided != set(expected):
        return (
            f"{artifact} metadata extras {sorted(provided)!r} != {sorted(expected)!r}"
        )

    observed = {extra: set() for extra in provided}
    for requirement in metadata.get_all("Requires-Dist", []):
        match = re.search(r";\s*extra\s*==\s*['\"]([^'\"]+)['\"]\s*$", requirement)
        if match is None:
            return f"unexpected runtime dependency in {artifact}: {requirement}"
        extra = match.group(1)
        if extra not in expected:
            return f"{artifact} metadata has dependency for unknown extra {extra!r}"
        observed[extra].add(_normalize_requirement(requirement[: match.start()]))

    for extra, expected_requirements in sorted(expected.items()):
        if observed.get(extra, set()) != expected_requirements:
            return f"{artifact} metadata dependencies for extra {extra!r} are stale"

    return None


def _check_core_metadata(
    metadata: email.message.Message, project: dict[str, Any], artifact: str
) -> str | None:
    if metadata.get("Name") != project["name"]:
        return (
            f"{artifact} metadata name {metadata.get('Name')!r} != {project['name']!r}"
        )
    if metadata.get("Version") != project["version"]:
        return (
            f"{artifact} metadata version {metadata.get('Version')!r} "
            f"!= {project['version']!r}"
        )
    if metadata.get("Requires-Python") != project["requires-python"]:
        return f"{artifact} metadata Requires-Python is stale"
    if metadata.get("Summary") != project["description"]:
        return f"{artifact} metadata Summary is stale"
    if metadata.get("Author") != project["authors"][0]["name"]:
        return f"{artifact} metadata Author is stale"
    if metadata.get("License-Expression") != project["license"]:
        return f"{artifact} metadata license is stale"
    if "LICENSE" not in metadata.get_all("License-File", []):
        return f"{artifact} metadata is missing License-File: LICENSE"
    keywords = [
        keyword.strip()
        for keyword in (metadata.get("Keywords") or "").split(",")
        if keyword.strip()
    ]
    if set(keywords) != set(project.get("keywords", [])):
        return f"{artifact} metadata keywords are stale"
    if metadata.get_all("Classifier", []) != project.get("classifiers", []):
        return f"{artifact} metadata classifiers are stale"
    if metadata.get_all("Project-URL"):
        return f"{artifact} should not publish project URLs"

    if error := _check_optional_dependency_metadata(metadata, project, artifact):
        return error

    return None


def _record_sha256(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _check_wheel_record(archive: zipfile.ZipFile, names: set[str]) -> str | None:
    record_paths = [name for name in names if name.endswith(".dist-info/RECORD")]
    if len(record_paths) != 1:
        return f"wheel should contain exactly one RECORD file, got {len(record_paths)}"

    record_path = record_paths[0]
    rows = list(csv.reader(archive.read(record_path).decode("utf-8").splitlines()))
    recorded: set[str] = set()
    for row in rows:
        if len(row) != 3:
            return f"wheel RECORD has malformed row: {row!r}"
        path, digest, size = row
        if path in recorded:
            return f"wheel RECORD has duplicate entry for {path}"
        if path not in names:
            return f"wheel RECORD references missing file {path}"
        if path.endswith("/"):
            return f"wheel RECORD should not include directory entry {path}"
        recorded.add(path)

        if path == record_path:
            if digest or size:
                return "wheel RECORD entry must not carry its own hash or size"
            continue

        data = archive.read(path)
        expected_digest = f"sha256={_record_sha256(data)}"
        if digest != expected_digest:
            return f"wheel RECORD hash mismatch for {path}"
        if size != str(len(data)):
            return f"wheel RECORD size mismatch for {path}"

    archive_files = {name for name in names if not name.endswith("/")}
    missing = sorted(archive_files - recorded)
    if missing:
        return f"wheel RECORD is missing files: {', '.join(missing[:5])}"
    return None


def _check_wheel_license(
    archive: zipfile.ZipFile, names: set[str], root: Path
) -> str | None:
    license_paths = [
        name for name in names if name.endswith(".dist-info/licenses/LICENSE")
    ]
    if len(license_paths) != 1:
        return (
            "wheel should contain exactly one dist-info license file, "
            f"got {len(license_paths)}"
        )
    if archive.read(license_paths[0]) != (root / "LICENSE").read_bytes():
        return "wheel license file content mismatch"
    return None


def _check_wheel_build_metadata(metadata: email.message.Message) -> str | None:
    if metadata.get("Wheel-Version") != "1.0":
        return "wheel WHEEL metadata has unexpected Wheel-Version"
    if metadata.get("Root-Is-Purelib") != "true":
        return "wheel must install as purelib"
    if metadata.get_all("Tag", []) != ["py3-none-any"]:
        return "wheel must have exactly one py3-none-any tag"
    return None


def _wheel_runtime_modules(names: set[str]) -> list[str]:
    modules = ["techrevati.runtime"]
    runtime_prefix = "techrevati/runtime/"
    for name in sorted(names):
        if not name.startswith(runtime_prefix):
            continue
        relative = name.removeprefix(runtime_prefix)
        if "/" in relative or not relative.endswith(".py") or relative == "__init__.py":
            continue
        modules.append(f"techrevati.runtime.{Path(relative).stem}")
    return modules


def _check_wheel_import_smoke(
    wheel: Path, project: dict[str, Any], modules: list[str]
) -> str | None:
    smoke = """
import importlib
import importlib.resources
import sys

wheel = sys.argv[1]
expected_version = sys.argv[2]
modules = sys.argv[3:]

sys.path.insert(0, wheel)
runtime = importlib.import_module("techrevati.runtime")
if runtime.__version__ != expected_version:
    raise SystemExit(
        f"runtime version {runtime.__version__!r} != {expected_version!r}"
    )
for module_name in modules:
    importlib.import_module(module_name)
pricing = importlib.resources.files("techrevati.runtime").joinpath(
    "data", "pricing.json"
)
if not pricing.is_file():
    raise SystemExit("pricing.json is not available as a package resource")
pricing.read_text(encoding="utf-8")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            smoke,
            str(wheel.resolve()),
            project["version"],
            *modules,
        ],
        capture_output=True,
        check=False,
        cwd=wheel.parent,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"exit code {result.returncode}"
        return f"wheel import smoke failed: {reason}"
    return None


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _check_wheel_install_smoke(
    wheel: Path, project: dict[str, Any], modules: list[str], source_root: Path
) -> str | None:
    smoke = """
import importlib
import importlib.metadata
import importlib.resources
import pathlib
import sys

package_name = sys.argv[1]
expected_version = sys.argv[2]
source_root = pathlib.Path(sys.argv[3]).resolve()
modules = sys.argv[4:]

metadata_version = importlib.metadata.version(package_name)
if metadata_version != expected_version:
    raise SystemExit(
        f"installed metadata version {metadata_version!r} != {expected_version!r}"
    )
runtime = importlib.import_module("techrevati.runtime")
if runtime.__version__ != expected_version:
    raise SystemExit(
        f"runtime version {runtime.__version__!r} != {expected_version!r}"
    )
runtime_file = pathlib.Path(runtime.__file__).resolve()
try:
    runtime_file.relative_to(source_root)
except ValueError:
    pass
else:
    raise SystemExit(f"runtime imported from source tree: {runtime_file}")
for module_name in modules:
    importlib.import_module(module_name)
pricing = importlib.resources.files("techrevati.runtime").joinpath(
    "data", "pricing.json"
)
if not pricing.is_file():
    raise SystemExit("pricing.json is not available as a package resource")
pricing.read_text(encoding="utf-8")
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        venv_dir = temp_path / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        python = _venv_python(venv_dir)
        if not python.is_file():
            return f"isolated install smoke could not find venv Python: {python}"

        install = subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheel.resolve()),
            ],
            capture_output=True,
            check=False,
            cwd=temp_path,
            text=True,
            timeout=60,
        )
        if install.returncode != 0:
            detail = (install.stderr or install.stdout).strip().splitlines()
            reason = detail[-1] if detail else f"exit code {install.returncode}"
            return f"isolated wheel install failed: {reason}"

        result = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                smoke,
                project["name"],
                project["version"],
                str(source_root.resolve()),
                *modules,
            ],
            capture_output=True,
            check=False,
            cwd=temp_path,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            reason = detail[-1] if detail else f"exit code {result.returncode}"
            return f"isolated wheel smoke failed: {reason}"

    return None


def _scan_sdist_public_text(archive: tarfile.TarFile) -> str | None:
    for member in archive.getmembers():
        if not member.isfile():
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        if error := _scan_text(member.name, handle.read()):
            return error
    return None


def _check_wheel_package_files(
    names: set[str], root: Path, archive: zipfile.ZipFile | None = None
) -> str | None:
    expected_files = _source_package_file_contents(root)
    missing = sorted(set(expected_files) - names)
    if missing:
        return f"wheel is missing source package files: {', '.join(missing[:5])}"
    if archive is not None:
        for package_file, expected_data in sorted(expected_files.items()):
            if archive.read(package_file) != expected_data:
                return f"wheel source package file content mismatch: {package_file}"
    return None


def _check_sdist_package_files(
    names: set[str], root: Path, archive: tarfile.TarFile | None = None
) -> str | None:
    expected_files = _source_package_file_contents(root)
    for package_file, expected_data in sorted(expected_files.items()):
        suffix = f"src/{package_file}"
        archive_name = _match_suffix(names, suffix)
        if archive_name is None:
            return f"sdist is missing source package file: {package_file}"
        if archive is not None:
            handle = archive.extractfile(archive_name)
            if handle is None:
                return f"sdist source package file could not be read: {package_file}"
            if handle.read() != expected_data:
                return f"sdist source package file content mismatch: {package_file}"
    return None


def _check_sdist_file_content(
    names: set[str], root: Path, archive: tarfile.TarFile, suffix: str
) -> str | None:
    archive_name = _match_suffix(names, suffix)
    if archive_name is None:
        return f"sdist is missing required file ending with {suffix!r}"

    handle = archive.extractfile(archive_name)
    if handle is None:
        return f"sdist required file could not be read: {suffix}"
    if handle.read() != (root / suffix).read_bytes():
        return f"sdist required file content mismatch: {suffix}"
    return None


def _check_sdist_required_file_contents(
    names: set[str], root: Path, archive: tarfile.TarFile
) -> str | None:
    for suffix in sorted(_required_sdist_suffixes(root)):
        if error := _check_sdist_file_content(names, root, archive, suffix):
            return error
    return None


def _check_sdist_surface(names: set[str], expected_stem: str) -> str | None:
    prefix = f"{expected_stem}/"
    for name in sorted(names):
        relative = name.removeprefix(prefix)
        parts = set(Path(relative).parts)
        forbidden = sorted(parts & FORBIDDEN_SDIST_PARTS)
        if forbidden:
            return f"sdist contains forbidden path component {forbidden[0]!r}: {name}"
    return None


def _check_wheel(wheel: Path, project: dict[str, Any], root: Path) -> str | None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

        if error := _check_wheel_surface(names, project):
            return error

        missing = sorted(REQUIRED_WHEEL_FILES - names)
        if missing:
            return f"wheel is missing required files: {', '.join(missing)}"

        if error := _check_wheel_package_files(names, root, archive):
            return error

        forbidden = [
            name for name in names if name.endswith(".pyc") or "__pycache__/" in name
        ]
        if forbidden:
            return f"wheel contains bytecode/cache files: {', '.join(forbidden[:5])}"

        metadata_paths = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            return (
                "wheel should contain exactly one METADATA file, "
                f"got {len(metadata_paths)}"
            )

        wheel_paths = [name for name in names if name.endswith(".dist-info/WHEEL")]
        if len(wheel_paths) != 1:
            return (
                f"wheel should contain exactly one WHEEL file, got {len(wheel_paths)}"
            )

        metadata = email.parser.Parser().parsestr(
            archive.read(metadata_paths[0]).decode("utf-8")
        )
        if error := _check_core_metadata(metadata, project, "wheel"):
            return error

        wheel_metadata = email.parser.Parser().parsestr(
            archive.read(wheel_paths[0]).decode("utf-8")
        )
        if error := _check_wheel_build_metadata(wheel_metadata):
            return error

        if error := _check_wheel_license(archive, names, root):
            return error

        if error := _check_wheel_record(archive, names):
            return error

        if error := _scan_wheel_public_text(archive):
            return error

        if error := _check_wheel_import_smoke(
            wheel, project, _wheel_runtime_modules(names)
        ):
            return error
        if error := _check_wheel_install_smoke(
            wheel, project, _wheel_runtime_modules(names), root
        ):
            return error

    return None


def _check_sdist(sdist: Path, project: dict[str, Any], root: Path) -> str | None:
    expected_stem = f"{_distribution_prefix(project)}-{project['version']}"

    with tarfile.open(sdist) as archive:
        names = set(archive.getnames())

        valid_prefix = all(
            name == expected_stem or name.startswith(f"{expected_stem}/")
            for name in names
        )
        if not valid_prefix:
            return "sdist contains paths outside the expected top-level directory"

        links = [
            member.name
            for member in archive.getmembers()
            if member.issym() or member.islnk()
        ]
        if links:
            return f"sdist contains links: {', '.join(links[:5])}"

        if error := _check_sdist_surface(names, expected_stem):
            return error

        if error := _check_sdist_package_files(names, root, archive):
            return error

        metadata_paths = [name for name in names if name.endswith("/PKG-INFO")]
        if len(metadata_paths) != 1:
            return (
                "sdist should contain exactly one PKG-INFO file, "
                f"got {len(metadata_paths)}"
            )
        metadata_handle = archive.extractfile(metadata_paths[0])
        if metadata_handle is None:
            return "sdist PKG-INFO could not be read"
        metadata = email.parser.Parser().parsestr(
            metadata_handle.read().decode("utf-8")
        )
        if error := _check_core_metadata(metadata, project, "sdist"):
            return error

        if error := _check_sdist_required_file_contents(names, root, archive):
            return error

        forbidden = [
            name for name in names if name.endswith(".pyc") or "__pycache__/" in name
        ]
        if forbidden:
            return f"sdist contains bytecode/cache files: {', '.join(forbidden[:5])}"

        if error := _scan_sdist_public_text(archive):
            return error

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dist",
        nargs="?",
        default="dist",
        type=Path,
        help="Directory containing exactly one wheel and one sdist.",
    )
    args = parser.parse_args()

    dist = args.dist
    root = Path.cwd()
    if not dist.is_dir():
        return _fail(f"{dist} is not a directory")

    wheel = _find_single(dist, "*.whl")
    if wheel is None:
        return _fail("expected exactly one wheel in dist/")

    sdist = _find_single(dist, "*.tar.gz")
    if sdist is None:
        return _fail("expected exactly one .tar.gz sdist in dist/")

    project = _load_project_metadata(root)

    if error := _check_artifact_names(wheel, sdist, project):
        return _fail(error)
    if error := _check_wheel(wheel, project, root):
        return _fail(error)
    if error := _check_sdist(sdist, project, root):
        return _fail(error)

    print(f"Distribution artifacts OK: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
