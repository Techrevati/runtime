"""Validate release evidence packaging and checksum manifests."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import re
import sys
import tarfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ project
    import tomli as tomllib  # type: ignore[no-redef]

DOCS_REQUIRED_SNIPPETS = (
    "python scripts/check_release_evidence.py dist",
    "reviewed project metadata",
    "(cd dist && sha256sum -c SHA256SUMS)",
    "SHA256SUMS",
)
DOCS_FORBIDDEN_LINES = ("sha256sum -c SHA256SUMS",)
PLAN_REQUIRED_SNIPPETS = (
    "release-context evidence smoke",
    "isolated temporary virtual environment",
    "python scripts/check_release_evidence.py dist",
    "`SHA256SUMS` generation",
    "SBOM JSON and XML generation",
)
WORKFLOW_REQUIRED_SNIPPETS = (
    "python scripts/check_release_evidence.py",
    "python scripts/check_release_evidence.py dist",
    "sha256sum *.whl *.tar.gz sbom.cyclonedx.json sbom.cyclonedx.xml > SHA256SUMS",
    "dist/SHA256SUMS",
)
CHECKSUM_LINE_RE = re.compile(r"^(?P<hash>[0-9a-f]{64})\s+\*?(?P<name>.+)$")
WHEEL_NAME_RE = re.compile(
    r"^(?P<name>.+)-(?P<version>[^-]+)(?:-[^-]+)?-[^-]+-[^-]+-[^-]+\.whl$"
)
SDIST_NAME_RE = re.compile(r"^(?P<name>.+)-(?P<version>[^-]+)\.tar\.gz$")
REQUIRED_EVIDENCE_NAMES = {
    "sbom.cyclonedx.json",
    "sbom.cyclonedx.xml",
    "SHA256SUMS",
}
ArtifactIdentity = tuple[str, str]


def _distribution_prefix(project_name: str) -> str:
    return re.sub(r"[-_.]+", "_", project_name).lower()


def _load_expected_identity(root: Path) -> ArtifactIdentity | None:
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    with pyproject_path.open("rb") as handle:
        pyproject: dict[str, Any] = tomllib.load(handle)
    project = pyproject.get("project", {})
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        return None
    return (_distribution_prefix(name), version)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _single_match(dist: Path, pattern: str) -> Path | None:
    matches = sorted(path for path in dist.glob(pattern) if path.is_file())
    if len(matches) != 1:
        return None
    return matches[0]


def _wheel_identity(path: Path) -> ArtifactIdentity | None:
    match = WHEEL_NAME_RE.fullmatch(path.name)
    if match is None:
        return None
    return (match.group("name").lower(), match.group("version"))


def _sdist_identity(path: Path) -> ArtifactIdentity | None:
    match = SDIST_NAME_RE.fullmatch(path.name)
    if match is None:
        return None
    return (match.group("name").lower(), match.group("version"))


def _check_artifact_identity(
    wheel: Path,
    sdist: Path,
    *,
    expected_identity: ArtifactIdentity | None = None,
) -> list[str]:
    wheel_identity = _wheel_identity(wheel)
    sdist_identity = _sdist_identity(sdist)
    failures: list[str] = []
    if wheel_identity is None:
        failures.append(f"wheel filename is not parseable: {wheel.name}")
    if sdist_identity is None:
        failures.append(f"source archive filename is not parseable: {sdist.name}")
    if failures:
        return failures

    if wheel_identity != sdist_identity:
        failures.append(
            f"wheel and source archive identity mismatch: {wheel.name} != {sdist.name}"
        )

    if expected_identity is not None and wheel_identity != expected_identity:
        expected_prefix, expected_version = expected_identity
        failures.append(
            "release artifact identity does not match project metadata: "
            f"{wheel_identity[0]} {wheel_identity[1]} != "
            f"{expected_prefix} {expected_version}"
        )

    return failures


def _metadata_identity(text: str) -> ArtifactIdentity:
    metadata = email.parser.Parser().parsestr(text)
    return (
        _distribution_prefix(metadata.get("Name", "")),
        metadata.get("Version", ""),
    )


def _check_wheel_metadata_identity(
    wheel: Path,
    identity: ArtifactIdentity,
) -> list[str]:
    expected_name, expected_version = identity
    expected_metadata = f"{expected_name}-{expected_version}.dist-info/METADATA"
    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata_files = sorted(
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            )
            if metadata_files != [expected_metadata]:
                return [
                    "wheel metadata path does not match artifact identity: "
                    f"{metadata_files!r} != {[expected_metadata]!r}"
                ]
            observed = _metadata_identity(
                archive.read(expected_metadata).decode("utf-8", errors="replace")
            )
    except zipfile.BadZipFile:
        return [f"wheel archive is not readable: {wheel.name}"]

    if observed != identity:
        return [
            "wheel metadata identity does not match artifact filename: "
            f"{observed[0]} {observed[1]} != {expected_name} {expected_version}"
        ]
    return []


def _check_sdist_metadata_identity(
    sdist: Path,
    identity: ArtifactIdentity,
) -> list[str]:
    expected_name, expected_version = identity
    expected_root = f"{expected_name}-{expected_version}"
    expected_pkg_info = f"{expected_root}/PKG-INFO"
    try:
        with tarfile.open(sdist, "r:gz") as archive:
            names = sorted(archive.getnames())
            roots = sorted({name.split("/", 1)[0] for name in names if name})
            if roots != [expected_root]:
                return [
                    "source archive root does not match artifact identity: "
                    f"{roots!r} != {[expected_root]!r}"
                ]
            try:
                member = archive.extractfile(expected_pkg_info)
            except KeyError:
                member = None
            if member is None:
                return [f"source archive is missing {expected_pkg_info}"]
            observed = _metadata_identity(
                member.read().decode("utf-8", errors="replace")
            )
    except tarfile.TarError as exc:
        return [f"source archive is not readable: {sdist.name}: {exc}"]

    if observed != identity:
        return [
            "source archive metadata identity does not match artifact filename: "
            f"{observed[0]} {observed[1]} != {expected_name} {expected_version}"
        ]
    return []


def _parse_checksum_manifest(path: Path) -> tuple[dict[str, str], list[str]]:
    checksums: dict[str, str] = {}
    failures: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            failures.append(f"SHA256SUMS line {line_number} is empty")
            continue
        match = CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            failures.append(f"SHA256SUMS line {line_number} is invalid")
            continue
        name = match.group("name")
        if "/" in name or "\\" in name or name in {".", ".."}:
            failures.append(f"SHA256SUMS line {line_number} uses unsafe path: {name}")
            continue
        if name in checksums:
            failures.append(f"SHA256SUMS has duplicate entry: {name}")
            continue
        checksums[name] = match.group("hash")
    return checksums, failures


def _check_sbom_json(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path.name} is not valid JSON: {exc.msg}"]

    failures: list[str] = []
    if not isinstance(payload, dict):
        return [f"{path.name} must contain a JSON object"]
    if payload.get("bomFormat") != "CycloneDX":
        failures.append(f"{path.name} is not a CycloneDX JSON BOM")
    if not isinstance(payload.get("specVersion"), str):
        failures.append(f"{path.name} is missing CycloneDX specVersion")
    return failures


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


def _check_sbom_xml(path: Path) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return [f"{path.name} is not valid XML: {exc}"]

    failures: list[str] = []
    if _xml_local_name(root.tag) != "bom":
        failures.append(f"{path.name} root element must be CycloneDX bom")
    if "cyclonedx.org/schema/bom" not in root.tag:
        failures.append(f"{path.name} is not a CycloneDX XML BOM")
    if not root.attrib.get("version"):
        failures.append(f"{path.name} is missing BOM version")
    return failures


def _check_release_evidence_dir(
    dist: Path,
    *,
    expected_identity: ArtifactIdentity | None = None,
) -> list[str]:
    if not dist.is_dir():
        return [f"{dist} is not a directory"]

    wheel = _single_match(dist, "*.whl")
    sdist = _single_match(dist, "*.tar.gz")
    failures: list[str] = []
    if wheel is None:
        failures.append("release evidence must contain exactly one wheel")
    if sdist is None:
        failures.append("release evidence must contain exactly one source archive")

    for name in sorted(REQUIRED_EVIDENCE_NAMES):
        path = dist / name
        if not path.is_file():
            failures.append(f"release evidence is missing {name}")
        elif path.stat().st_size == 0:
            failures.append(f"release evidence file is empty: {name}")

    if failures:
        return failures
    assert wheel is not None
    assert sdist is not None

    failures.extend(
        _check_artifact_identity(
            wheel,
            sdist,
            expected_identity=expected_identity,
        )
    )
    wheel_identity = _wheel_identity(wheel)
    sdist_identity = _sdist_identity(sdist)
    if wheel_identity is not None and sdist_identity == wheel_identity:
        failures.extend(_check_wheel_metadata_identity(wheel, wheel_identity))
        failures.extend(_check_sdist_metadata_identity(sdist, wheel_identity))

    expected_names = {
        wheel.name,
        sdist.name,
        *REQUIRED_EVIDENCE_NAMES,
    }
    actual_names = {path.name for path in dist.iterdir() if path.is_file()}
    if actual_names != expected_names:
        unexpected = sorted(actual_names - expected_names)
        missing = sorted(expected_names - actual_names)
        if unexpected:
            failures.append(f"release evidence has unexpected files: {unexpected}")
        if missing:
            failures.append(f"release evidence is missing files: {missing}")

    checksums, manifest_failures = _parse_checksum_manifest(dist / "SHA256SUMS")
    failures.extend(manifest_failures)
    manifest_names = set(checksums)
    hash_expected_names = expected_names - {"SHA256SUMS"}
    if manifest_names != hash_expected_names:
        unexpected = sorted(manifest_names - hash_expected_names)
        missing = sorted(hash_expected_names - manifest_names)
        if unexpected:
            failures.append(f"SHA256SUMS has unexpected entries: {unexpected}")
        if missing:
            failures.append(f"SHA256SUMS is missing entries: {missing}")

    for name, expected_hash in sorted(checksums.items()):
        path = dist / name
        if path.is_file() and _sha256(path) != expected_hash:
            failures.append(f"SHA256 mismatch for {name}")

    failures.extend(_check_sbom_json(dist / "sbom.cyclonedx.json"))
    failures.extend(_check_sbom_xml(dist / "sbom.cyclonedx.xml"))

    return failures


def _missing_snippets(text: str, snippets: tuple[str, ...]) -> list[str]:
    text_lower = text.lower()
    return [snippet for snippet in snippets if snippet.lower() not in text_lower]


def _check_text_file(root: Path, path: Path, snippets: tuple[str, ...]) -> list[str]:
    full_path = root / path
    if not full_path.is_file():
        return [f"{path.as_posix()} is missing"]
    text = full_path.read_text(encoding="utf-8")
    failures = [
        f"{path.as_posix()} is missing release evidence text: {snippet}"
        for snippet in _missing_snippets(text, snippets)
    ]
    lines = {line.strip() for line in text.splitlines()}
    failures.extend(
        f"{path.as_posix()} contains root-relative release evidence command: {line}"
        for line in DOCS_FORBIDDEN_LINES
        if line in lines
    )
    return failures


def _failures(root: Path) -> list[str]:
    failures = _check_text_file(
        root,
        Path("SECURITY.md"),
        DOCS_REQUIRED_SNIPPETS,
    )
    failures.extend(
        _check_text_file(
            root,
            Path("docs/compliance/private-rc-publication.md"),
            DOCS_REQUIRED_SNIPPETS,
        )
    )
    failures.extend(
        _check_text_file(
            root,
            Path("docs/compliance/stable-promotion.md"),
            ("SHA256SUMS evidence",),
        )
    )
    failures.extend(
        _check_text_file(
            root,
            Path("docs/compliance/production-readiness.md"),
            PLAN_REQUIRED_SNIPPETS,
        )
    )
    failures.extend(
        _check_text_file(
            root,
            Path(".github/workflows/release.yml"),
            WORKFLOW_REQUIRED_SNIPPETS,
        )
    )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dist",
        nargs="?",
        type=Path,
        help="Optional release evidence directory to validate.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Project root containing release evidence docs and workflows.",
    )
    args = parser.parse_args()

    failures = (
        _check_release_evidence_dir(
            args.dist,
            expected_identity=_load_expected_identity(args.root),
        )
        if args.dist
        else _failures(args.root)
    )
    if failures:
        print("release evidence check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Release evidence check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
