# Rollback Execution Checklist

Author: Techrevati doo

Rollback execution status: Pending until rollback is proven in the pilot
environment.

This checklist controls the real rollback proof for `0.3.0rc1`. Command-shape
proof and local dry-run output do not prove rollback. The previous known-good
package must be installed in the downstream pilot environment and verified with
smoke, durable evidence, and checkpoint behavior.

## Purpose

Rollback execution proves that operators can move from `0.3.0rc1` back to the
previous known-good runtime package without losing required audit evidence or
leaving the pilot workflow in an unknown state.

## Rollback Preflight Snapshot

Latest rollback preflight snapshot collected on 2026-06-01 before the real
pilot-environment rollback:

- branch: `production-rc-0.3.0`,
- base HEAD before staging: `1d57f9c33b6980321d21a20078f2a1ac9a7ed3da`,
- current working-tree release diff: 85 files changed, 8,859 insertions,
  1,794 deletions,
- untracked release assets: 104 files, all classified by
  `docs/compliance/staging-manifest.md`,
- current release candidate: `0.3.0rc1`,
- rollback command shape: documented with `--no-index`, `--no-deps`, and a
  controlled artifact directory,
- local pilot dry-run rollback readiness command shape: passed,
- rollback execution guard: passed,
- pilot execution guard: passed,
- current package build, distribution checks, SBOM generation, and checksum
  evidence: passed locally/server-side,
- previous known-good version, previous known-good artifact source, downstream
  worker drain/stop proof, version proof, smoke result, durable event/usage
  proof, checkpoint proof, and operator sign-off: Pending.

This is preflight evidence only. It confirms the rollback procedure shape and
local checklist readiness, but rollback remains unproven until the previous
known-good package is installed and validated in the downstream pilot
environment.

## Execution Boundary

Do not mark rollback as proven from documentation, command-shape checks, local
dry-run output, or package build success. Rollback is proven only when the
downstream pilot environment installs the previous known-good version and the
post-rollback smoke checks pass.

## Preconditions

All of these must be true before rollback execution:

- previous known-good version is approved for the pilot workflow,
- previous known-good wheel and source archive are available from the private
  artifact source,
- current package version and commit SHA are recorded,
- `events.db`, `usage.db`, `checkpoints.db`, and process logs are preserved,
- downstream worker can be stopped or drained,
- rollback operator and pilot owner approve the rollback window,
- smoke test command is available,
- rollback artifact source is reachable without a public package index fallback.

## Evidence Preservation

Before changing the package version, preserve restricted evidence for:

- current package version,
- current commit SHA,
- production gate evidence,
- remote CI validation evidence,
- private RC publication evidence,
- event records,
- usage records,
- checkpoint records or checkpoint IDs,
- process logs for the rollback window,
- pilot profile values,
- incident or proof-window notes.

Do not attach prompts, tool arguments, tool outputs, raw secrets, credentials,
or unredacted customer data to the rollback record.

## Execution Steps

Record the exact rollback command:

```bash
python -m pip install --no-index --no-deps --find-links "$RUNTIME_ARTIFACT_DIR" \
  "techrevati-runtime==$TECHREVATI_RUNTIME_ROLLBACK_VERSION"
```

The command must install from the controlled artifact channel and must not
resolve dependencies from a public package index.

## Verification Commands

Record the version proof:

```bash
python -c "import importlib.metadata; print(importlib.metadata.version('techrevati-runtime'))"
```

Expected output: the previous known-good version.

Record the downstream smoke result:

- downstream worker starts,
- package version matches rollback target,
- one successful session completes,
- event sink writes a new event,
- usage sink writes a new usage record,
- no public branding regression appears,
- no secret appears in logs.

## Resume And Checkpoint Proof

If the pilot workflow uses checkpoints, prove one of these outcomes:

- a session can resume from a checkpoint created before rollback,
- a session can start cleanly after rollback when resume is intentionally
  disabled.

Record the `thread_id`, checkpoint ID, resume mode, and result.

## Failure Handling

If rollback fails, stop pilot expansion and record:

- failure point,
- operator,
- package versions involved,
- preserved evidence location,
- mitigation owner,
- next rollback attempt window,
- whether current pilot traffic must be stopped.

## No-Go Rules

Do not promote stable `0.3.0` when any of these are true:

- rollback execution status is still pending,
- command-shape proof is the only rollback evidence,
- previous known-good version is unknown,
- private artifact source is missing,
- rollback install used a public package index fallback,
- downstream smoke test failed,
- durable event or usage evidence failed after rollback,
- checkpoint behavior is unknown,
- logs contain raw secrets or unredacted customer data,
- rollback proof is not linked from the pilot evidence record.

## Sign-Off Template

Use this template for the rollback execution record:

| Field | Value |
|---|---|
| Rollback operator | Pending |
| Pilot owner | Pending |
| Current version | `0.3.0rc1` |
| Previous known-good version | Pending |
| Artifact source | Pending |
| Rollback window | Pending |
| Version proof result | Pending |
| Smoke result | Pending |
| Event/usage proof result | Pending |
| Checkpoint proof result | Pending |
| Open issues | Pending |
| Decision | Pending / Approved / Changes required |
