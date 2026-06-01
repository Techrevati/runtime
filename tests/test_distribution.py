from __future__ import annotations

import email.parser
import importlib.util
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _load_distribution_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_distribution.py"
    )
    spec = importlib.util.spec_from_file_location("check_distribution", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_required_sdist_suffixes_include_dynamic_scripts_and_tests(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    scripts = tmp_path / "scripts"
    tests = tmp_path / "tests"
    examples = tmp_path / "examples"
    scripts.mkdir()
    tests.mkdir()
    examples.mkdir()
    (scripts / "check_alpha.py").write_text("", encoding="utf-8")
    (scripts / "check_beta.py").write_text("", encoding="utf-8")
    (scripts / "helper.py").write_text("", encoding="utf-8")
    (tests / "test_alpha.py").write_text("", encoding="utf-8")
    (tests / "helper.py").write_text("", encoding="utf-8")
    (examples / "tiny_agent.py").write_text("", encoding="utf-8")
    (examples / "pricing.json").write_text("{}", encoding="utf-8")
    (examples / "cached.pyc").write_bytes(b"cache")

    suffixes = module._required_sdist_suffixes(tmp_path)

    assert "scripts/check_alpha.py" in suffixes
    assert "scripts/check_beta.py" in suffixes
    assert "scripts/helper.py" not in suffixes
    assert "tests/test_alpha.py" in suffixes
    assert "tests/helper.py" not in suffixes
    assert "examples/tiny_agent.py" in suffixes
    assert "examples/pricing.json" in suffixes
    assert "examples/cached.pyc" not in suffixes
    assert "pyproject.toml" in suffixes


def test_required_sdist_suffixes_include_dynamic_documentation_files(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    (tmp_path / "mkdocs.yml").write_text("site_name: Runtime\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs_theme = tmp_path / "docs_theme"
    cache = docs / "__pycache__"
    docs.mkdir()
    docs_theme.mkdir()
    cache.mkdir()
    (docs / "index.md").write_text("# Runtime\n", encoding="utf-8")
    (docs / "api.md").write_text("# API\n", encoding="utf-8")
    (docs / "runtime.css").write_text("body {}\n", encoding="utf-8")
    (docs_theme / "main.html").write_text("<main></main>\n", encoding="utf-8")
    (cache / "index.pyc").write_bytes(b"cache")

    suffixes = module._required_sdist_suffixes(tmp_path)

    assert "mkdocs.yml" in suffixes
    assert "docs/index.md" in suffixes
    assert "docs/api.md" in suffixes
    assert "docs/runtime.css" in suffixes
    assert "docs_theme/main.html" in suffixes
    assert "docs/__pycache__/index.pyc" not in suffixes


def test_source_package_files_include_runtime_tree(tmp_path: Path) -> None:
    module = _load_distribution_module()
    package_dir = tmp_path / "src" / "techrevati" / "runtime"
    cache_dir = package_dir / "__pycache__"
    cache_dir.mkdir(parents=True)
    (tmp_path / "src" / "techrevati" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    (package_dir / "alpha.py").write_text("", encoding="utf-8")
    (package_dir / "py.typed").write_text("", encoding="utf-8")
    (cache_dir / "alpha.pyc").write_bytes(b"cache")

    assert module._source_package_files(tmp_path) == {
        "techrevati/__init__.py",
        "techrevati/runtime/alpha.py",
        "techrevati/runtime/py.typed",
    }


def test_check_wheel_package_files_rejects_missing_source_module(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    package_dir = tmp_path / "src" / "techrevati" / "runtime"
    package_dir.mkdir(parents=True)
    (tmp_path / "src" / "techrevati" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    (package_dir / "alpha.py").write_text("", encoding="utf-8")

    error = module._check_wheel_package_files({"techrevati/__init__.py"}, tmp_path)

    assert error is not None
    assert "techrevati/runtime/alpha.py" in error


def test_check_wheel_package_files_rejects_stale_source_content(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    package_dir = tmp_path / "src" / "techrevati" / "runtime"
    package_dir.mkdir(parents=True)
    source_file = package_dir / "alpha.py"
    source_file.write_text("VALUE = 2\n", encoding="utf-8")
    wheel = tmp_path / "sample.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("techrevati/runtime/alpha.py", "VALUE = 1\n")

    with zipfile.ZipFile(wheel) as archive:
        error = module._check_wheel_package_files(
            set(archive.namelist()), tmp_path, archive
        )

    assert error is not None
    assert "content mismatch" in error


def test_check_sdist_package_files_rejects_missing_source_module(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    package_dir = tmp_path / "src" / "techrevati" / "runtime"
    package_dir.mkdir(parents=True)
    (tmp_path / "src" / "techrevati" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )
    (package_dir / "alpha.py").write_text("", encoding="utf-8")

    error = module._check_sdist_package_files(
        {"sample-1.2.3/src/techrevati/__init__.py"},
        tmp_path,
    )

    assert error is not None
    assert "techrevati/runtime/alpha.py" in error


def test_check_sdist_package_files_rejects_stale_source_content(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    package_dir = tmp_path / "src" / "techrevati" / "runtime"
    package_dir.mkdir(parents=True)
    source_file = package_dir / "alpha.py"
    source_file.write_text("VALUE = 2\n", encoding="utf-8")
    sdist = tmp_path / "sample.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        payload = b"VALUE = 1\n"
        member = tarfile.TarInfo("sample-1.2.3/src/techrevati/runtime/alpha.py")
        member.size = len(payload)
        archive.addfile(member, fileobj=io.BytesIO(payload))

    with tarfile.open(sdist) as archive:
        error = module._check_sdist_package_files(
            set(archive.getnames()), tmp_path, archive
        )

    assert error is not None
    assert "content mismatch" in error


def test_check_sdist_file_content_accepts_matching_required_file(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    docs = tmp_path / "docs"
    docs.mkdir()
    source_file = docs / "index.md"
    source_file.write_text("# Runtime\n", encoding="utf-8")
    sdist = tmp_path / "sample.tar.gz"
    payload = source_file.read_bytes()
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("sample-1.2.3/docs/index.md")
        member.size = len(payload)
        archive.addfile(member, fileobj=io.BytesIO(payload))

    with tarfile.open(sdist) as archive:
        assert (
            module._check_sdist_file_content(
                set(archive.getnames()), tmp_path, archive, "docs/index.md"
            )
            is None
        )


def test_check_sdist_file_content_rejects_stale_required_file(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text("# Runtime v2\n", encoding="utf-8")
    sdist = tmp_path / "sample.tar.gz"
    payload = b"# Runtime v1\n"
    with tarfile.open(sdist, "w:gz") as archive:
        member = tarfile.TarInfo("sample-1.2.3/docs/index.md")
        member.size = len(payload)
        archive.addfile(member, fileobj=io.BytesIO(payload))

    with tarfile.open(sdist) as archive:
        error = module._check_sdist_file_content(
            set(archive.getnames()), tmp_path, archive, "docs/index.md"
        )

    assert error is not None
    assert "content mismatch" in error


def test_has_suffix_matches_top_level_sdist_directory() -> None:
    module = _load_distribution_module()
    names = {
        "techrevati_runtime-1.2.3/scripts/check_alpha.py",
        "techrevati_runtime-1.2.3/pyproject.toml",
    }

    assert module._has_suffix(names, "scripts/check_alpha.py")
    assert module._has_suffix(names, "pyproject.toml")
    assert not module._has_suffix(names, "scripts/check_missing.py")


def test_check_artifact_names_accepts_expected_names() -> None:
    module = _load_distribution_module()
    project = {"name": "techrevati-runtime", "version": "1.2.3"}

    assert (
        module._check_artifact_names(
            Path("techrevati_runtime-1.2.3-py3-none-any.whl"),
            Path("techrevati_runtime-1.2.3.tar.gz"),
            project,
        )
        is None
    )


def test_check_artifact_names_rejects_stale_filename_version() -> None:
    module = _load_distribution_module()
    project = {"name": "techrevati-runtime", "version": "1.2.3"}

    error = module._check_artifact_names(
        Path("techrevati_runtime-9.9.9-py3-none-any.whl"),
        Path("techrevati_runtime-1.2.3.tar.gz"),
        project,
    )

    assert error is not None
    assert "wheel filename" in error


def test_check_wheel_surface_accepts_package_and_dist_info_only() -> None:
    module = _load_distribution_module()
    project = {"name": "techrevati-runtime", "version": "1.2.3"}
    names = {
        "techrevati/__init__.py",
        "techrevati/runtime/__init__.py",
        "techrevati_runtime-1.2.3.dist-info/METADATA",
        "techrevati_runtime-1.2.3.dist-info/licenses/LICENSE",
    }

    assert module._check_wheel_surface(names, project) is None


def test_check_wheel_surface_rejects_accidental_test_payload() -> None:
    module = _load_distribution_module()
    project = {"name": "techrevati-runtime", "version": "1.2.3"}

    error = module._check_wheel_surface(
        {
            "techrevati/__init__.py",
            "tests/test_runtime.py",
            "techrevati_runtime-1.2.3.dist-info/METADATA",
        },
        project,
    )

    assert error is not None
    assert "unexpected top-level file" in error


def test_check_sdist_surface_accepts_source_release_files() -> None:
    module = _load_distribution_module()
    names = {
        "techrevati_runtime-1.2.3/pyproject.toml",
        "techrevati_runtime-1.2.3/src/techrevati/runtime/__init__.py",
        "techrevati_runtime-1.2.3/tests/test_runtime.py",
        "techrevati_runtime-1.2.3/docs/index.md",
    }

    assert module._check_sdist_surface(names, "techrevati_runtime-1.2.3") is None


def test_check_sdist_surface_rejects_cache_payload() -> None:
    module = _load_distribution_module()
    error = module._check_sdist_surface(
        {
            "techrevati_runtime-1.2.3/pyproject.toml",
            "techrevati_runtime-1.2.3/.mypy_cache/cache.db",
        },
        "techrevati_runtime-1.2.3",
    )

    assert error is not None
    assert "forbidden path component" in error


def test_wheel_runtime_modules_lists_importable_runtime_modules() -> None:
    module = _load_distribution_module()
    names = {
        "techrevati/__init__.py",
        "techrevati/runtime/__init__.py",
        "techrevati/runtime/alpha.py",
        "techrevati/runtime/beta.py",
        "techrevati/runtime/data/pricing.json",
        "techrevati/runtime/submodule/gamma.py",
    }

    assert module._wheel_runtime_modules(names) == [
        "techrevati.runtime",
        "techrevati.runtime.alpha",
        "techrevati.runtime.beta",
    ]


def test_wheel_import_smoke_imports_package_modules_and_resource(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    wheel = tmp_path / "sample-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("techrevati/__init__.py", "")
        archive.writestr("techrevati/runtime/__init__.py", '__version__ = "1.2.3"\n')
        archive.writestr("techrevati/runtime/alpha.py", "VALUE = 1\n")
        archive.writestr("techrevati/runtime/data/pricing.json", "{}\n")

    assert (
        module._check_wheel_import_smoke(
            wheel,
            {"version": "1.2.3"},
            ["techrevati.runtime", "techrevati.runtime.alpha"],
        )
        is None
    )


def test_wheel_install_smoke_uses_isolated_no_deps_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_distribution_module()
    wheel = tmp_path / "sample-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    commands: list[list[str]] = []
    cwd_values: list[Path] = []

    class FakeEnvBuilder:
        def __init__(self, *, with_pip: bool, clear: bool) -> None:
            assert with_pip is True
            assert clear is True

        def create(self, venv_dir: Path) -> None:
            python = module._venv_python(venv_dir)
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")

    def fake_run(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        cwd = kwargs["cwd"]
        assert isinstance(cwd, Path)
        cwd_values.append(cwd)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.venv, "EnvBuilder", FakeEnvBuilder)
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    error = module._check_wheel_install_smoke(
        wheel,
        {"name": "techrevati-runtime", "version": "1.2.3"},
        ["techrevati.runtime", "techrevati.runtime.alpha"],
        tmp_path / "source",
    )

    assert error is None
    assert len(commands) == 2
    install_command = commands[0]
    assert install_command[1:] == [
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
        str(wheel.resolve()),
    ]
    smoke_command = commands[1]
    assert smoke_command[1:4] == ["-I", "-c", smoke_command[3]]
    assert smoke_command[4:] == [
        "techrevati-runtime",
        "1.2.3",
        str((tmp_path / "source").resolve()),
        "techrevati.runtime",
        "techrevati.runtime.alpha",
    ]
    assert all(cwd != tmp_path for cwd in cwd_values)


def test_check_wheel_record_accepts_matching_hashes_and_sizes(
    tmp_path: Path,
) -> None:
    module = _load_distribution_module()
    wheel = tmp_path / "sample-1.2.3-py3-none-any.whl"
    payload = b"VALUE = 1\n"
    record_path = "sample-1.2.3.dist-info/RECORD"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("sample.py", payload)
        archive.writestr(
            record_path,
            "\n".join(
                (
                    f"sample.py,sha256={module._record_sha256(payload)},{len(payload)}",
                    f"{record_path},,",
                )
            ),
        )

    with zipfile.ZipFile(wheel) as archive:
        assert module._check_wheel_record(archive, set(archive.namelist())) is None


def test_check_wheel_record_rejects_hash_mismatch(tmp_path: Path) -> None:
    module = _load_distribution_module()
    wheel = tmp_path / "sample-1.2.3-py3-none-any.whl"
    record_path = "sample-1.2.3.dist-info/RECORD"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("sample.py", b"VALUE = 1\n")
        archive.writestr(
            record_path,
            "\n".join(
                (
                    "sample.py,sha256=invalid,10",
                    f"{record_path},,",
                )
            ),
        )

    with zipfile.ZipFile(wheel) as archive:
        error = module._check_wheel_record(archive, set(archive.namelist()))

    assert error is not None
    assert "RECORD hash mismatch" in error


def test_check_wheel_license_accepts_matching_license(tmp_path: Path) -> None:
    module = _load_distribution_module()
    (tmp_path / "LICENSE").write_text("MIT\n", encoding="utf-8")
    wheel = tmp_path / "sample-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("sample-1.2.3.dist-info/licenses/LICENSE", "MIT\n")

    with zipfile.ZipFile(wheel) as archive:
        assert (
            module._check_wheel_license(archive, set(archive.namelist()), tmp_path)
            is None
        )


def test_check_wheel_license_rejects_stale_license(tmp_path: Path) -> None:
    module = _load_distribution_module()
    (tmp_path / "LICENSE").write_text("MIT\n", encoding="utf-8")
    wheel = tmp_path / "sample-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("sample-1.2.3.dist-info/licenses/LICENSE", "Apache\n")

    with zipfile.ZipFile(wheel) as archive:
        error = module._check_wheel_license(
            archive,
            set(archive.namelist()),
            tmp_path,
        )

    assert error is not None
    assert "license file content mismatch" in error


def test_check_wheel_build_metadata_accepts_pure_python_tag() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Wheel-Version: 1.0",
                "Root-Is-Purelib: true",
                "Tag: py3-none-any",
            )
        )
    )

    assert module._check_wheel_build_metadata(metadata) is None


def test_check_wheel_build_metadata_rejects_platform_tag() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Wheel-Version: 1.0",
                "Root-Is-Purelib: false",
                "Tag: cp312-cp312-manylinux_x86_64",
            )
        )
    )

    error = module._check_wheel_build_metadata(metadata)

    assert error is not None
    assert "purelib" in error


def test_core_metadata_rejects_stale_sdist_version() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Name: techrevati-runtime",
                "Version: 9.9.9",
                "Summary: Async-aware runtime primitives for multi-step "
                "LLM agent loops.",
                "Author: Techrevati doo",
                "License-Expression: MIT",
                "Requires-Python: >=3.11",
            )
        )
    )
    project = {
        "name": "techrevati-runtime",
        "version": "1.2.3",
        "description": "Async-aware runtime primitives for multi-step LLM agent loops.",
        "authors": [{"name": "Techrevati doo"}],
        "license": "MIT",
        "requires-python": ">=3.11",
    }

    error = module._check_core_metadata(metadata, project, "sdist")

    assert error is not None
    assert "sdist metadata version" in error


def test_core_metadata_rejects_stale_keywords() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Name: techrevati-runtime",
                "Version: 1.2.3",
                "Summary: Async-aware runtime primitives for multi-step "
                "LLM agent loops.",
                "Author: Techrevati doo",
                "License-Expression: MIT",
                "License-File: LICENSE",
                "Keywords: agents,stale",
                "Classifier: Typing :: Typed",
                "Requires-Python: >=3.11",
            )
        )
    )
    project = {
        "name": "techrevati-runtime",
        "version": "1.2.3",
        "description": "Async-aware runtime primitives for multi-step LLM agent loops.",
        "authors": [{"name": "Techrevati doo"}],
        "license": "MIT",
        "requires-python": ">=3.11",
        "keywords": ["agents", "runtime"],
        "classifiers": ["Typing :: Typed"],
        "optional-dependencies": {},
    }

    error = module._check_core_metadata(metadata, project, "sdist")

    assert error is not None
    assert "metadata keywords are stale" in error


def test_core_metadata_rejects_stale_classifiers() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Name: techrevati-runtime",
                "Version: 1.2.3",
                "Summary: Async-aware runtime primitives for multi-step "
                "LLM agent loops.",
                "Author: Techrevati doo",
                "License-Expression: MIT",
                "License-File: LICENSE",
                "Keywords: agents,runtime",
                "Classifier: Typing :: Stubs Only",
                "Requires-Python: >=3.11",
            )
        )
    )
    project = {
        "name": "techrevati-runtime",
        "version": "1.2.3",
        "description": "Async-aware runtime primitives for multi-step LLM agent loops.",
        "authors": [{"name": "Techrevati doo"}],
        "license": "MIT",
        "requires-python": ">=3.11",
        "keywords": ["agents", "runtime"],
        "classifiers": ["Typing :: Typed"],
        "optional-dependencies": {},
    }

    error = module._check_core_metadata(metadata, project, "sdist")

    assert error is not None
    assert "metadata classifiers are stale" in error


def test_core_metadata_accepts_exact_optional_dependencies() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Name: techrevati-runtime",
                "Version: 1.2.3",
                "Summary: Async-aware runtime primitives for multi-step "
                "LLM agent loops.",
                "Author: Techrevati doo",
                "License-Expression: MIT",
                "License-File: LICENSE",
                "Keywords: agents,runtime",
                "Classifier: Typing :: Typed",
                "Requires-Python: >=3.11",
                "Provides-Extra: otel",
                "Requires-Dist: opentelemetry-api<2,>=1.27; extra == 'otel'",
            )
        )
    )
    project = {
        "name": "techrevati-runtime",
        "version": "1.2.3",
        "description": "Async-aware runtime primitives for multi-step LLM agent loops.",
        "authors": [{"name": "Techrevati doo"}],
        "license": "MIT",
        "requires-python": ">=3.11",
        "keywords": ["agents", "runtime"],
        "classifiers": ["Typing :: Typed"],
        "optional-dependencies": {"otel": ["opentelemetry-api>=1.27,<2"]},
    }

    assert module._check_core_metadata(metadata, project, "sdist") is None


def test_core_metadata_rejects_stale_optional_dependency() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Name: techrevati-runtime",
                "Version: 1.2.3",
                "Summary: Async-aware runtime primitives for multi-step "
                "LLM agent loops.",
                "Author: Techrevati doo",
                "License-Expression: MIT",
                "License-File: LICENSE",
                "Keywords: agents,runtime",
                "Classifier: Typing :: Typed",
                "Requires-Python: >=3.11",
                "Provides-Extra: dev",
                "Requires-Dist: pytest==8.0.0; extra == 'dev'",
            )
        )
    )
    project = {
        "name": "techrevati-runtime",
        "version": "1.2.3",
        "description": "Async-aware runtime primitives for multi-step LLM agent loops.",
        "authors": [{"name": "Techrevati doo"}],
        "license": "MIT",
        "requires-python": ">=3.11",
        "keywords": ["agents", "runtime"],
        "classifiers": ["Typing :: Typed"],
        "optional-dependencies": {"dev": ["pytest==9.0.3"]},
    }

    error = module._check_core_metadata(metadata, project, "sdist")

    assert error is not None
    assert "dependencies for extra 'dev' are stale" in error


def test_core_metadata_rejects_runtime_dependency() -> None:
    module = _load_distribution_module()
    metadata = email.parser.Parser().parsestr(
        "\n".join(
            (
                "Name: techrevati-runtime",
                "Version: 1.2.3",
                "Summary: Async-aware runtime primitives for multi-step "
                "LLM agent loops.",
                "Author: Techrevati doo",
                "License-Expression: MIT",
                "License-File: LICENSE",
                "Keywords: agents,runtime",
                "Classifier: Typing :: Typed",
                "Requires-Python: >=3.11",
                "Requires-Dist: requests>=2",
            )
        )
    )
    project = {
        "name": "techrevati-runtime",
        "version": "1.2.3",
        "description": "Async-aware runtime primitives for multi-step LLM agent loops.",
        "authors": [{"name": "Techrevati doo"}],
        "license": "MIT",
        "requires-python": ">=3.11",
        "keywords": ["agents", "runtime"],
        "classifiers": ["Typing :: Typed"],
        "optional-dependencies": {},
    }

    error = module._check_core_metadata(metadata, project, "sdist")

    assert error is not None
    assert "unexpected runtime dependency" in error


def test_check_sdist_rejects_archive_links(tmp_path: Path) -> None:
    module = _load_distribution_module()
    sdist = tmp_path / "techrevati_runtime-1.2.3.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        link = tarfile.TarInfo("techrevati_runtime-1.2.3/docs/changelog.md")
        link.type = tarfile.SYMTYPE
        link.linkname = "../CHANGELOG.md"
        archive.addfile(link)

    error = module._check_sdist(
        sdist,
        {"name": "techrevati-runtime", "version": "1.2.3"},
        tmp_path,
    )

    assert error is not None
    assert "sdist contains links" in error
