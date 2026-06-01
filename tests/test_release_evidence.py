from __future__ import annotations

import hashlib
import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType


def _load_release_evidence_module() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "check_release_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("check_release_evidence", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_evidence_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata_text(name: str = "techrevati-runtime", version: str = "1.2.3") -> str:
    return f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n"


def _write_wheel(
    path: Path,
    *,
    name: str = "techrevati_runtime",
    metadata_name: str = "techrevati-runtime",
    version: str = "1.2.3",
) -> None:
    dist_info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{dist_info}/METADATA",
            _metadata_text(metadata_name, version),
        )
        archive.writestr("techrevati/runtime/__init__.py", "")


def _write_sdist(
    path: Path,
    *,
    name: str = "techrevati_runtime",
    metadata_name: str = "techrevati-runtime",
    version: str = "1.2.3",
) -> None:
    root = f"{name}-{version}"
    payload = _metadata_text(metadata_name, version).encode()
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(f"{root}/PKG-INFO")
        member.size = len(payload)
        archive.addfile(member, fileobj=io.BytesIO(payload))


def _write_valid_evidence(dist: Path) -> None:
    dist.mkdir()
    _write_wheel(dist / "techrevati_runtime-1.2.3-py3-none-any.whl")
    _write_sdist(dist / "techrevati_runtime-1.2.3.tar.gz")
    _write_evidence_file(
        dist / "sbom.cyclonedx.json",
        b'{"bomFormat":"CycloneDX","specVersion":"1.6"}',
    )
    _write_evidence_file(
        dist / "sbom.cyclonedx.xml",
        b'<bom xmlns="http://cyclonedx.org/schema/bom/1.6" version="1"/>',
    )
    (dist / "SHA256SUMS").write_text(
        "\n".join(
            f"{_digest(path)}  {path.name}"
            for path in sorted(dist.iterdir())
            if path.name != "SHA256SUMS"
        ),
        encoding="utf-8",
    )


def _rewrite_manifest(dist: Path) -> None:
    (dist / "SHA256SUMS").write_text(
        "\n".join(
            f"{_digest(path)}  {path.name}"
            for path in sorted(dist.iterdir())
            if path.name != "SHA256SUMS"
        ),
        encoding="utf-8",
    )


def _write_docs_fixture(root: Path) -> None:
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "docs" / "compliance").mkdir(parents=True)
    required = (
        "python scripts/check_release_evidence.py dist\n"
        "reviewed project metadata\n"
        "(cd dist && sha256sum -c SHA256SUMS)\n"
        "SHA256SUMS"
    )
    (root / "SECURITY.md").write_text(required, encoding="utf-8")
    (root / "docs" / "compliance" / "private-rc-publication.md").write_text(
        required,
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "stable-promotion.md").write_text(
        "SHA256SUMS evidence",
        encoding="utf-8",
    )
    (root / "docs" / "compliance" / "production-readiness.md").write_text(
        "\n".join(
            (
                "release-context evidence smoke",
                "isolated temporary virtual environment",
                "python scripts/check_release_evidence.py dist",
                "`SHA256SUMS` generation",
                "SBOM JSON and XML generation",
            )
        ),
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "release.yml").write_text(
        "\n".join(
            (
                "python scripts/check_release_evidence.py",
                "python scripts/check_release_evidence.py dist",
                "sha256sum *.whl *.tar.gz sbom.cyclonedx.json "
                "sbom.cyclonedx.xml > SHA256SUMS",
                "dist/SHA256SUMS",
            )
        ),
        encoding="utf-8",
    )


def test_release_evidence_accepts_valid_manifest(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)

    assert module._check_release_evidence_dir(dist) == []


def test_release_evidence_rejects_hash_mismatch(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    (dist / "techrevati_runtime-1.2.3-py3-none-any.whl").write_bytes(b"tampered")

    failures = module._check_release_evidence_dir(dist)

    assert any("SHA256 mismatch" in failure for failure in failures)


def test_release_evidence_rejects_missing_manifest_entry(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    manifest = dist / "SHA256SUMS"
    manifest.write_text(
        "\n".join(
            line
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if "sbom.cyclonedx.xml" not in line
        ),
        encoding="utf-8",
    )

    failures = module._check_release_evidence_dir(dist)

    assert any("missing entries" in failure for failure in failures)


def test_release_evidence_rejects_extra_file(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    (dist / "debug.log").write_text("extra", encoding="utf-8")

    failures = module._check_release_evidence_dir(dist)

    assert any("unexpected files" in failure for failure in failures)


def test_release_evidence_rejects_unsafe_manifest_path(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    manifest = dist / "SHA256SUMS"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\n" + "0" * 64 + "  ../bad",
        encoding="utf-8",
    )

    failures = module._check_release_evidence_dir(dist)

    assert any("unsafe path" in failure for failure in failures)


def test_release_evidence_rejects_wheel_sdist_identity_mismatch(
    tmp_path: Path,
) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    (dist / "techrevati_runtime-1.2.3.tar.gz").rename(
        dist / "techrevati_runtime-1.2.4.tar.gz"
    )
    _rewrite_manifest(dist)

    failures = module._check_release_evidence_dir(dist)

    assert any("identity mismatch" in failure for failure in failures)


def test_release_evidence_rejects_project_metadata_identity_mismatch(
    tmp_path: Path,
) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)

    failures = module._check_release_evidence_dir(
        dist,
        expected_identity=("techrevati_runtime", "1.2.4"),
    )

    assert any("project metadata" in failure for failure in failures)


def test_release_evidence_rejects_wheel_metadata_identity_mismatch(
    tmp_path: Path,
) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    _write_wheel(
        dist / "techrevati_runtime-1.2.3-py3-none-any.whl",
        version="9.9.9",
    )
    _rewrite_manifest(dist)

    failures = module._check_release_evidence_dir(dist)

    assert any("wheel metadata" in failure for failure in failures)


def test_release_evidence_rejects_sdist_metadata_identity_mismatch(
    tmp_path: Path,
) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    _write_sdist(dist / "techrevati_runtime-1.2.3.tar.gz", version="9.9.9")
    _rewrite_manifest(dist)

    failures = module._check_release_evidence_dir(dist)

    assert any("source archive root" in failure for failure in failures)


def test_release_evidence_rejects_invalid_sbom_json(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    (dist / "sbom.cyclonedx.json").write_text("{not-json", encoding="utf-8")
    _rewrite_manifest(dist)

    failures = module._check_release_evidence_dir(dist)

    assert any(
        "sbom.cyclonedx.json is not valid JSON" in failure for failure in failures
    )


def test_release_evidence_rejects_non_cyclonedx_json(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    (dist / "sbom.cyclonedx.json").write_text(
        '{"bomFormat":"Other","specVersion":"1.6"}',
        encoding="utf-8",
    )
    _rewrite_manifest(dist)

    failures = module._check_release_evidence_dir(dist)

    assert any("not a CycloneDX JSON BOM" in failure for failure in failures)


def test_release_evidence_rejects_invalid_sbom_xml(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    (dist / "sbom.cyclonedx.xml").write_text("<bom", encoding="utf-8")
    _rewrite_manifest(dist)

    failures = module._check_release_evidence_dir(dist)

    assert any("sbom.cyclonedx.xml is not valid XML" in failure for failure in failures)


def test_release_evidence_rejects_non_cyclonedx_xml(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    dist = tmp_path / "dist"
    _write_valid_evidence(dist)
    (dist / "sbom.cyclonedx.xml").write_text("<bom version='1'/>", encoding="utf-8")
    _rewrite_manifest(dist)

    failures = module._check_release_evidence_dir(dist)

    assert any("not a CycloneDX XML BOM" in failure for failure in failures)


def test_release_evidence_accepts_docs_fixture(tmp_path: Path) -> None:
    module = _load_release_evidence_module()
    _write_docs_fixture(tmp_path)

    assert module._failures(tmp_path) == []


def test_release_evidence_rejects_missing_workflow_verifier(
    tmp_path: Path,
) -> None:
    module = _load_release_evidence_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / ".github" / "workflows" / "release.yml").write_text(
        "dist/SHA256SUMS",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any("check_release_evidence.py dist" in failure for failure in failures)


def test_release_evidence_rejects_missing_production_gate_parity(
    tmp_path: Path,
) -> None:
    module = _load_release_evidence_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "docs" / "compliance" / "production-readiness.md").write_text(
        "python scripts/check_release_evidence.py dist",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any(
        "isolated temporary virtual environment" in failure for failure in failures
    )


def test_release_evidence_rejects_root_relative_checksum_docs(
    tmp_path: Path,
) -> None:
    module = _load_release_evidence_module()
    _write_docs_fixture(tmp_path)
    (tmp_path / "SECURITY.md").write_text(
        "python scripts/check_release_evidence.py dist\n"
        "sha256sum -c SHA256SUMS\n"
        "SHA256SUMS",
        encoding="utf-8",
    )

    failures = module._failures(tmp_path)

    assert any(
        "(cd dist && sha256sum -c SHA256SUMS)" in failure for failure in failures
    )
