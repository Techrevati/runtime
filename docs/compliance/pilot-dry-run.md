# Pilot Dry-Run

Author: Techrevati doo

The pilot dry-run is a local smoke proof for the controlled `0.3.0rc1` pilot
shape. It validates runtime wiring before the real downstream pilot starts.
It does not replace the required controlled pilot or real rollback proof.

## Command

```bash
python scripts/check_pilot_dry_run.py --output pilot-dry-run.json
```

The command exits non-zero if any local scenario fails. The optional JSON output
can be attached to the restricted pilot evidence bundle.

## Covered Scenarios

The dry-run covers:

- successful session,
- prompt-injection attempt,
- permission denial,
- guardrail block,
- max-iterations breach,
- max-tool-calls breach,
- provider failover evidence,
- checkpoint resume,
- sink failure diagnostic,
- rollback readiness command shape.

Rollback readiness is intentionally limited to command-shape proof. The real
pilot still has to install the previous known-good package and pass the
downstream smoke test from the rollback proof checklist.

## Evidence

The dry-run creates temporary SQLite files for events, usage, and checkpoints.
It verifies that event and usage sinks can be fanned out, usage totals are
recorded, checkpoints can be replayed by `thread_id` and `idempotency_key`, and
sink failures are visible without stopping the session.

Do not use the dry-run output as a go decision by itself. The stable release
still requires the controlled RC pilot evidence template, rollback proof
checklist, remote CI, private release publication, and real downstream evidence.
