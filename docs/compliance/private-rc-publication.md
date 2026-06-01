# Private RC Publication

Author: Techrevati doo

Private RC publication status: Pending until `0.3.0rc1` is published to the
private channel.

This checklist controls how `techrevati-runtime` may be published as a release
candidate. It keeps the first production channel private, preserves artifact
evidence, and prevents accidental publication to a public package index before
pilot approval.

## Purpose

The release candidate is ready for review only after the local/server gate and
remote CI pass on the same commit. Publication is a separate production gate:
it proves that the exact wheel and source archive can be distributed through a
controlled channel without leaking credentials or bypassing artifact checks.

## Publication Preflight Snapshot

Latest private publication preflight snapshot collected on 2026-06-01 before
tagging or publishing:

- branch: `production-rc-0.3.0`,
- base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,
- current working-tree release diff: 85 files changed, 8,859 insertions,
  1,794 deletions,
- untracked release assets: 104 files, all classified by
  `docs/compliance/staging-manifest.md`,
- local/server full production gate: 999 tests passed with 94.85 percent total
  coverage,
- fresh wheel and source archive build for `0.3.0rc1`: passed,
- distribution artifact check against local `dist`: passed,
- package metadata rendering with explicit wheel and source archive
  `twine check`: passed,
- local SBOM JSON, SBOM XML, and `SHA256SUMS` generation: passed,
- release evidence guard against local `dist`: passed,
- checksum verification with `(cd dist && sha256sum -c SHA256SUMS)`: passed,
- temporary private-package upload staging with wheel and source archive only:
  passed,
- private-package staged artifact distribution and metadata checks: passed,
- remote CI, security reviewer sign-off, private registry secret validation,
  tag creation, repository release attachment, and private package publish:
  Pending until the reviewed release-candidate commit is available and the
  release workflow runs.

This is preflight evidence only. It verifies the local package and publication
shape, but it does not publish the release candidate or approve pilot use.

## Publication Boundary

For `0.3.0rc1`, publication is limited to:

- a private package registry or controlled internal artifact channel,
- repository release evidence containing wheel, source archive, SBOM JSON, SBOM
  XML, and `SHA256SUMS`,
- internal pilot consumers approved for the controlled RC pilot.

Public package index publication is out of scope until pilot approval is
recorded.

## Required Inputs

Publication requires all of these inputs:

- reviewed release-candidate diff,
- green full local/server production gate,
- green remote CI on the same commit,
- approved security review with no unresolved high or critical findings,
- valid `v0.3.0rc1` tag,
- private package repository URL in the CI secret store,
- private package username in the CI secret store,
- private package password or token in the CI secret store,
- rollback target from the previous known-good runtime version.

## Artifact Verification

The release workflow must build and verify exactly these package artifacts:

- `techrevati_runtime-0.3.0rc1-py3-none-any.whl`,
- `techrevati_runtime-0.3.0rc1.tar.gz`,
- `sbom.cyclonedx.json`,
- `sbom.cyclonedx.xml`,
- `SHA256SUMS`.

Required verification commands include:

```bash
python scripts/check_distribution.py dist
python scripts/check_release_evidence.py dist
python -m twine check dist/*.whl dist/*.tar.gz
python scripts/check_distribution.py private-package-dist
python -m twine check private-package-dist/*.whl private-package-dist/*.tar.gz
pip install --force-reinstall --no-index --no-deps --find-links dist techrevati-runtime
(cd dist && sha256sum -c SHA256SUMS)
```

The package upload directory must contain only wheel and source archive files.
SBOM files and `SHA256SUMS` stay attached to repository release evidence and
must not be uploaded as package artifacts. The release evidence verifier parses
both SBOM files and rejects evidence unless the JSON and XML files are valid
CycloneDX BOMs. It also verifies that package name and version match the
reviewed project metadata by reading the wheel and source archive metadata when
the verifier runs from the repository root.

## Private Channel Controls

The release workflow must publish to a configured private repository URL, not a
default public package index. The private repository URL, username, and
password/token must come from the CI secret store.

The workflow must fail before publish when any private channel input is empty.
It must not fall back to a default package index.

## Credential Handling

Private registry credentials must be short-lived where the provider supports
that model. They must never be committed, printed in logs, stored in release
artifacts, or copied into documentation examples.

Credential rotation is required when:

- the private registry token is exposed,
- the CI secret store is changed by an unauthorized actor,
- a release run prints or uploads credential material,
- the RC publication channel changes.

## Publication Procedure

1. Confirm final diff review is complete.
2. Confirm the full local/server production gate is green.
3. Confirm remote CI is green on the same commit.
4. Confirm the security review is approved with no unresolved high or critical
   findings.
5. Confirm version surfaces all reference `0.3.0rc1`.
6. Create the `v0.3.0rc1` tag only after review and CI are green.
7. Let the release workflow build wheel, source archive, and SBOM files.
8. Confirm distribution, metadata, and no-dependency install checks pass.
9. Confirm private repository URL, username, and password/token are present in
   the CI secret store.
10. Publish only wheel and source archive files to the private channel.
11. Attach wheel, source archive, SBOM JSON, SBOM XML, and `SHA256SUMS` to
    repository release evidence.
12. Record private RC publication evidence before starting the controlled pilot.

## No-Go Rules

Do not publish the release candidate when any of these are true:

- remote CI is missing or red on the reviewed commit,
- final diff review is incomplete,
- security review is unsigned,
- private package repository URL is empty,
- private package credentials are empty,
- workflow falls back to a default public package index,
- package upload includes SBOM files or local build leftovers,
- wheel or source archive fails distribution checks,
- `twine check` fails,
- no-dependency wheel install fails,
- checksum verification fails,
- public branding guard fails,
- rollback target is not known.

## Evidence Template

Use this template for the private RC publication record:

| Field | Value |
|---|---|
| Reviewer | Pending |
| Commit | Pending |
| Tag | `v0.3.0rc1` |
| Remote CI run | Pending |
| Security review result | Pending |
| Private package repository URL configured | Pending |
| Wheel verified | Pending |
| Source archive verified | Pending |
| SBOM JSON attached | Pending |
| SBOM XML attached | Pending |
| SHA256SUMS attached | Pending |
| Checksum verification | Pending |
| Private package publish result | Pending |
| Rollback target | Pending |
| Decision | Pending / Approved / Changes required |
