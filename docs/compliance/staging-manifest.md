# Staging Manifest

Author: Techrevati doo

Staging manifest status: Pending until final reviewer approves the staged
release set.

This manifest is the last repository-shape check before any stage, tag, private
publication, pilot, or stable promotion step. It does not replace final diff
review, reviewer handoff, remote CI, security review, private RC publication
evidence, controlled pilot evidence, or rollback proof.

## Purpose

The release-candidate diff contains a large set of new documentation pages,
guard scripts, tests, and one runtime pilot helper. Before the release candidate
is staged, every untracked release asset must be intentionally classified and
every local or generated artifact must remain excluded.

The manifest has three goals:

- make the release set reviewable before `git add`,
- prevent ignored or generated artifacts from entering the release diff,
- keep the final reviewer from approving a package with missing guard, docs, or
  evidence files.

## Allowed Untracked Release Assets

The only allowed untracked release assets are:

- `docs/api/*.md` API reference pages,
- `docs/compliance/*.md` compliance and release evidence pages,
- `docs/patterns/pilot-profile.md`,
- `docs/styles/*.css`,
- `docs_theme/*.html`,
- `scripts/check_*.py`,
- `scripts/release_preflight.py`,
- `scripts/install_toolchain.py`,
- `scripts/mkdocs_hooks/*.py`,
- `src/techrevati/runtime/pilot.py`,
- `tests/test_*.py`.

Anything outside those categories must be removed from the release candidate,
moved into the correct category, or explicitly added to this manifest with a
review reason before staging.

## Current Untracked Asset Snapshot

Latest pre-staging snapshot collected on 2026-06-01. The release candidate is
now committed on `production-rc-0.3.0`, so there are no remaining untracked
release assets — every asset that previously sat untracked (the categories
below) is part of the committed branch history. The live untracked set is
therefore empty and the counts are zero:

| Category | Count | Status |
|---|---:|---|
| `docs/api/*.md` | 0 | Allowed (committed) |
| `docs/compliance/*.md` | 0 | Allowed (committed) |
| `docs/patterns/pilot-profile.md` | 0 | Allowed (committed) |
| `docs/styles/*.css` | 0 | Allowed (committed) |
| `docs_theme/*.html` | 0 | Allowed (committed) |
| `scripts/check_*.py` | 0 | Allowed (committed) |
| `scripts/release_preflight.py` | 0 | Allowed (committed) |
| `scripts/install_toolchain.py` | 0 | Allowed (committed) |
| `scripts/mkdocs_hooks/*.py` | 0 | Allowed (committed) |
| `src/techrevati/runtime/pilot.py` | 0 | Allowed (committed) |
| `tests/test_*.py` release guard/test files | 0 | Allowed (committed) |
| Total | 0 | All release assets committed |

No generated/cache category is approved for staging. If `git status
--short --untracked-files=all` changes, regenerate this snapshot before
staging. The staging manifest guard compares these counts against the current
untracked release asset set.

## Generated Artifact Exclusions

Do not stage generated or local-only files, including:

- `.venv`,
- `dist`,
- `site`,
- `htmlcov`,
- `__pycache__`,
- `.pytest_cache`,
- `.ruff_cache`,
- `.mypy_cache`,
- `.coverage`,
- `.pyc` or `.pyo` files.

These files are build or local test outputs, not release-source assets.

## Review Procedure

Run and attach these commands before staging:

1. `git status --short --branch`
2. `git ls-files --others --exclude-standard`
3. `git diff --stat`
4. `python scripts/check_staging_manifest.py`

The reviewer must confirm that every untracked path is either one of the
allowed release assets above or has been removed before staging.

## No-Go Rules

Do not stage, tag, publish, pilot, or promote when any of these are true:

- staging manifest status is still pending after final reviewer review,
- untracked release assets are not classified by this manifest,
- generated artifacts or local caches appear in the untracked release set,
- `git status --short --branch` shows unexpected out-of-scope files,
- `git ls-files --others --exclude-standard` contains an unapproved path,
- `git diff --stat` does not match the reviewer-approved release scope,
- `scripts/check_staging_manifest.py` fails,
- the technical namespace `techrevati.runtime` is changed.

## Sign-Off Template

Use this template during release-candidate staging review:

| Field | Value |
|---|---|
| Reviewer | Pending |
| Review date | Pending |
| Commit SHA | Pending |
| Untracked release assets reviewed | Pending |
| Generated artifacts excluded | Pending |
| `git status --short --branch` attached | Pending |
| `git ls-files --others --exclude-standard` attached | Pending |
| `git diff --stat` attached | Pending |
| Staging manifest guard | Pending |
| Decision | Pending / Approved / Changes required |
