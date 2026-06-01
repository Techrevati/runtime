# Pilot Operations Runbook

Author: Techrevati doo

This runbook defines the minimum operating model for a controlled
`techrevati-runtime` release candidate pilot. It is scoped to a Python runtime
package embedded in one downstream workflow; it is not a hosted-service runbook.

## Scope

Use this runbook for `0.3.0rc1` pilots that run a limited internal support or
back-office workflow. The pilot must have bounded users, bounded volume,
non-destructive tool permissions by default, and a documented rollback target.

The pilot cannot start until the current commit has a green production gate,
remote CI has passed, and the package is available from a private registry or a
controlled artifact channel.

## Required Runtime Wiring

The pilot application should wire durable local records and telemetry at the
same time. Use durable SQLite sinks for audit and incident review. Add OTel
only after the deployment environment has an approved collector/exporter and
retention policy.

```python
from pathlib import Path

from techrevati.runtime import AgentSession, PermissionMode, SqliteSaver
from techrevati.runtime.persistence import SqliteEventSink, SqliteUsageSink
from techrevati.runtime.pilot import build_pilot_profile
from techrevati.runtime.sinks import FanoutEventSink, FanoutUsageSink

try:
    from techrevati.runtime.otel import OpenTelemetrySink, OpenTelemetryUsageSink
except ImportError:
    OpenTelemetrySink = None
    OpenTelemetryUsageSink = None

state_dir = Path("/var/lib/techrevati-runtime/pilot")
state_dir.mkdir(parents=True, exist_ok=True)

event_sinks = [SqliteEventSink(state_dir / "events.db")]
usage_sinks = [SqliteUsageSink(state_dir / "usage.db")]
if OpenTelemetrySink is not None and OpenTelemetryUsageSink is not None:
    event_sinks.append(OpenTelemetrySink())
    usage_sinks.append(OpenTelemetryUsageSink())

profile = build_pilot_profile(
    role="support_agent",
    allowed_tools=("lookup_case", "summarize_case"),
    budget_usd=5.00,
    permission_mode=PermissionMode.READ_ONLY,
    max_iterations=25,
    max_tool_calls=100,
    max_consecutive_failures=3,
)

agent = AgentSession(
    role="support_agent",
    phase="pilot",
    budget_usd=5.00,
    enforce_budget=True,
    event_sink=FanoutEventSink(tuple(event_sinks)),
    usage_sink=FanoutUsageSink(tuple(usage_sinks)),
    saver=SqliteSaver(state_dir / "checkpoints.db"),
    **profile.agent_session_kwargs(),
)

with agent.session(thread_id="pilot-session-001") as session:
    session.run_turn(
        lambda: "ok",
        model="pilot-model",
        idempotency_key="turn-001",
    )
```

The default fan-out behavior attempts every sink and then re-raises the first
sink error. `AgentSession` catches sink errors and keeps the session alive, so
the pilot keeps running while local diagnostics and logs still expose the
failure. Set `suppress_errors=True` only for a sink path that is already
monitored elsewhere.

## Minimal OTel Collector

Use one collector per pilot environment. Export outside the host only through an
approved backend.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
      http:

processors:
  batch:

exporters:
  logging:
    verbosity: basic

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [logging]
```

Do not enable `OpenTelemetrySink(include_event_detail=True)` unless event
details have been reviewed for sensitive data and telemetry retention has been
approved.

## Signal Map

| Signal | Source | Query or metric |
|---|---|---|
| Session count | `agent_events` | Count `agent.started` rows |
| Success rate | `agent_events` | `agent.completed` divided by started sessions |
| Failure rate | `agent_events` | `agent.failed` divided by started sessions |
| Failure-class distribution | `agent_events` | Group terminal `agent.failed` rows by `failure_class` |
| Guardrail blocks | `agent_events` | `agent.blocked` with payload data kind `guardrail` |
| Permission denials | `agent_events` | `agent.blocked` with payload data kind `permission` |
| Governance breaches | `agent_events` | Count `governance.breach` rows and terminal `failure_class=governance_breach` |
| Retry attempts | `agent_events` | Count `agent.recovery.attempted` rows |
| Provider switches | `agent_events` | Count `agent.recovery.provider_switched` rows |
| Token usage | `usage_records` and OTel | Sum input, output, cache-write, and cache-read tokens |
| Estimated cost | `usage_records` and OTel | Sum `cost_usd` |
| Tool call count | `usage_records` and events | Sum `total_tool_calls`; compare with `agent.tool_called` |
| Checkpoint writes | `checkpoints` table | Count new checkpoint rows per hour |
| Checkpoint replays | application log | Count idempotency-cache hits around `thread_id` and `idempotency_key` |
| Checkpoint persistence failures | `agent_events` and runtime log | Investigate terminal `failure_class=dependency_failed` rows with persistence error types |
| Event sink failures | runtime log and local session events | Search `event_sink failed; session continued` |
| Usage sink failures | `agent_events` and runtime log | Search `usage_sink failed; session continued` |
| Turn latency | OTel spans or wrapper metric | Measure each `session.run_turn` or `session.arun_turn` call |
| Tool-call latency | OTel spans or wrapper metric | Measure each `session.call_tool` or `session.acall_tool` call |

Turn latency and tool-call latency should be measured by the pilot wrapper if
the deployment does not export OTel spans. Keep the metric labels to role,
phase, tool, model, and outcome; do not attach prompts, tool arguments, or tool
outputs.

## Alert Rules

Minimum pilot alerts:

| Alert | Threshold | First action |
|---|---|---|
| Runtime exception spike | More than 1 percent `agent.failed` over 15 minutes, or any P0 error | Pause new sessions and inspect latest failures |
| Governance terminate spike | More than 3 `governance.breach` rows in 15 minutes | Pause the affected role and inspect limits |
| Cost threshold breach | 80 percent of pilot budget or any `BudgetExceededError` | Stop new sessions and review usage |
| Provider failure spike | More than 5 recovery attempts or provider switches in 15 minutes | Switch to the fallback provider or pause the pilot |
| Sink persistence failure | Any event sink failures or usage sink failures | Preserve process logs and verify SQLite write access |
| Checkpoint persistence failure | Any checkpoint write failure or unexpected persistence `dependency_failed` terminal event | Pause restart-sensitive sessions and verify SQLite write access before continuing |
| OTel export failure | Any collector export failure for 10 minutes | Keep SQLite evidence, restart collector, and avoid changing runtime code |

Any P0 incident or unresolved P1 incident is a no-go for stable `0.3.0`.

## Retention

Keep pilot evidence long enough to debug release-candidate behavior while
avoiding unnecessary personal-data retention:

| Record | Retention | Notes |
|---|---|---|
| `events.db` | 90 days after pilot close | Metadata-only event payloads; no prompts or tool outputs |
| `usage.db` | 90 days after pilot close | Token and cost aggregates only |
| `checkpoints.db` | 30 days after pilot close | May contain model results; review before sharing |
| OTel traces and metrics | 14 days | Keep detail fields disabled by default |
| Diagnostic bundle | Until go/no-go plus 30 days | Store in restricted release evidence |

Delete or rotate local SQLite files after the retention window. Do not attach
raw checkpoint payloads to public issues, changelogs, or package artifacts.

## Operator Procedures

### Version Check

```bash
python -c "import importlib.metadata; print(importlib.metadata.version('techrevati-runtime'))"
```

Expected pilot version: `0.3.0rc1`.

### Event Inspection

```bash
sqlite3 "$TECHREVATI_RUNTIME_EVENTS_DB" \
  "SELECT event, COUNT(*) FROM agent_events GROUP BY event ORDER BY COUNT(*) DESC;"
```

Inspect latest failures:

```bash
sqlite3 "$TECHREVATI_RUNTIME_EVENTS_DB" \
  "SELECT emitted_at, event, role, phase, payload FROM agent_events WHERE event IN ('agent.failed', 'governance.breach') ORDER BY id DESC LIMIT 20;"
```

Summarize terminal failure classes:

```bash
sqlite3 "$TECHREVATI_RUNTIME_EVENTS_DB" \
  "SELECT json_extract(payload, '$.failure_class') AS failure_class, COUNT(*) FROM agent_events WHERE event = 'agent.failed' GROUP BY failure_class ORDER BY COUNT(*) DESC;"
```

### Usage Inspection

```bash
sqlite3 "$TECHREVATI_RUNTIME_USAGE_DB" \
  "SELECT COUNT(*), ROUND(SUM(cost_usd), 6) FROM usage_records;"
```

Token totals can be inspected with `SqliteUsageSink.totals()` from a restricted
operator shell.

### Governance Breaches

```bash
sqlite3 "$TECHREVATI_RUNTIME_EVENTS_DB" \
  "SELECT emitted_at, role, phase, payload FROM agent_events WHERE event = 'governance.breach' ORDER BY id DESC LIMIT 20;"
```

If a breach repeats, stop the affected role, preserve the database files, and
open an RC bug with the limit name, observed value, ceiling, and scope.

### Sink Failure Triage

Search process logs for:

- `event_sink.emit raised; suppressing to keep session alive`
- `usage_sink.record raised; suppressing to keep session alive`
- `event fanout sink raised; continuing fanout`
- `usage fanout sink raised; continuing fanout`

Do not paste raw exception messages into public reports. Record only error
type, component, role, phase, version, and timestamp.

## Rollback

Before pilot start, record the previous known-good package version and artifact
location. The default rollback target is the last stable internal runtime build
approved for the downstream workflow.

Rollback steps:

1. Stop new pilot sessions.
2. Preserve `events.db`, `usage.db`, `checkpoints.db`, and process logs.
3. Reinstall the previous known-good package from the private registry or
   controlled artifact channel.
4. Restart the downstream worker with the previous known-good configuration.
5. Run the downstream smoke test.
6. Record rollback evidence in the pilot go/no-go note.

Example package rollback:

```bash
python -m pip install --no-index --no-deps --find-links "$RUNTIME_ARTIFACT_DIR" \
  "techrevati-runtime==$TECHREVATI_RUNTIME_ROLLBACK_VERSION"
```

Rollback is considered proven only after the downstream smoke test has passed
and new sessions are confirmed on the rollback version.

## Pilot Shutdown

Use pilot shutdown when any no-go condition appears, when cost reaches the
approved ceiling, or when telemetry evidence is incomplete.

Shutdown steps:

1. Disable new pilot traffic.
2. Let active sessions finish when safe; cancel them if a P0 is active.
3. Close SQLite sinks and flush OTel exporter buffers.
4. Collect the diagnostic bundle.
5. Record a go/no-go decision.
6. Open RC bugs for every stable-blocking issue.

## Diagnostic Bundle

For every incident or go/no-go review, collect:

- package version and commit SHA,
- production gate evidence,
- remote CI status,
- `events.db`,
- `usage.db`,
- relevant checkpoint IDs or a redacted `checkpoints.db`,
- process logs for the incident window,
- OTel trace or metric export for the incident window,
- configured pilot profile values,
- rollback target and rollback command.

Do not include prompts, tool arguments, tool outputs, raw secrets, credentials,
or unredacted customer data in the bundle.

## Acceptance Evidence

The pilot can move toward stable release only when:

- all minimum signals above are visible,
- alerts have been exercised or dry-run,
- rollback has been tested outside the production pilot,
- there are zero P0 incidents,
- there are zero unresolved P1 incidents,
- at least 99 percent of sessions complete without runtime crash,
- blocked, denied, breached, and recovered actions are audit-ready,
- `permission_denied`, `guardrail_violation`, and `governance_breach`
  classifications are visible for terminal policy and safety stops,
- usage and cost tracking are explainable,
- the go/no-go decision is recorded.
