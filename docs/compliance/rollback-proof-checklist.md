# Rollback Proof Checklist

Author: Techrevati doo

Use this checklist to prove that a `techrevati-runtime` release candidate can
be rolled back in the downstream pilot environment. A stable release cannot
proceed until rollback has been tested and documented.

Use `docs/compliance/rollback-execution.md` for the real rollback execution
record. This checklist is complete only when real rollback execution evidence is
attached from the pilot environment.

## Rollback Scope

| Field | Value |
|---|---|
| Pilot workflow | |
| Current package version | `0.3.0rc1` |
| Current commit SHA | |
| Previous known-good version | |
| Previous known-good artifact source | |
| Rollback operator | |
| Rollback start time | |
| Rollback end time | |

## Preconditions

- [ ] Previous known-good version is approved for the pilot workflow.
- [ ] Previous known-good wheel and sdist are available from a private registry
  or controlled artifact channel.
- [ ] Current `events.db`, `usage.db`, `checkpoints.db`, and process logs are
  preserved before rollback.
- [ ] The downstream worker can be stopped or drained.
- [ ] A smoke test exists for the downstream workflow.
- [ ] The pilot owner has approved the rollback window.

## Evidence Preservation

Before changing the package version, preserve:

- package version and commit SHA,
- production gate evidence,
- remote CI status,
- `events.db`,
- `usage.db`,
- `checkpoints.db` or relevant checkpoint IDs,
- process logs for the incident or proof window,
- OTel trace or metric export for the proof window,
- configured pilot profile values.

Do not attach prompts, tool arguments, tool outputs, raw secrets, credentials,
or unredacted customer data to public records.

## Rollback Command

Record the exact command used:

```bash
python -m pip install --no-index --no-deps --find-links "$RUNTIME_ARTIFACT_DIR" \
  "techrevati-runtime==$TECHREVATI_RUNTIME_ROLLBACK_VERSION"
```

## Version Proof

After rollback, record command output:

```bash
python -c "import importlib.metadata; print(importlib.metadata.version('techrevati-runtime'))"
```

Expected output: the previous known-good version.

## Smoke Test

| Check | Result | Notes |
|---|---|---|
| Downstream worker starts | | |
| Package version matches rollback target | | |
| One successful session completes | | |
| Event sink writes a new event | | |
| Usage sink writes a new usage record | | |
| Checkpoint saver can write | | |
| No public branding regression appears | | |
| No secret appears in logs | | |

## Resume And Checkpoint Proof

If the downstream workflow uses checkpoints, prove one of these:

- [ ] A session can resume from a checkpoint created before rollback.
- [ ] A session can start cleanly after rollback when resume is intentionally
  disabled.

Record the `thread_id`, checkpoint ID, and smoke result:

| Field | Value |
|---|---|
| Thread ID | |
| Checkpoint ID | |
| Resume mode | |
| Result | |

## Acceptance Criteria

- [ ] Previous known-good package version is installed.
- [ ] Downstream smoke test passes.
- [ ] New sessions run on the rollback version.
- [ ] Durable event and usage records still work.
- [ ] Checkpoint behavior is understood and documented.
- [ ] Rollback evidence is linked from the controlled RC pilot evidence note.

## Rollback Decision

Rollback proven: `yes` / `no`

Reason:

Open issues:

Operator:

Approval:

Date:
