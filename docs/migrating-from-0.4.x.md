# Migrating from 0.4.x to 0.5.0

0.5.0 fixes defects found in a full audit of the 0.4.0 line and adds tool timeouts,
tool replay, loop detection, provider fallback, and support for the MCP Python SDK
2.x. New API is additive, but several fixes change behaviour you may have relied
on. Read this list before upgrading.

## Sessions

- **Usage without a `model` now counts.** `run_turn(fn, usage=...)` used to discard
  the usage, which silently disabled `UsageLimits`. It now reaches the tracker, so a
  limit that never fired before may fire.
- **A paid turn is booked before limits run.** After a `BudgetExceededError` the
  governance plane and the checkpoint both include that turn, and when both a budget
  and a governance limit are crossed, `GovernanceBreachError` wins.
- **A replayed turn counts towards the budget** of the session that replays it.
- **Rate-limit tokens are taken later.** The iteration cap, governance, and
  `before_model` hooks now run first, so a rejected turn spends no token.
- **New `agent.turn.completed` and `agent.turn.failed` events.** Sinks and audit logs
  see more records. Code that compares a whole event list needs updating.
- **Tool events carry more data.** `tool_completed` and tool-failure events add
  `duration_seconds`, and failures add `error_type`. Compare keys, not whole dicts.
- **`complete()`, `fail()`, and `cancel()` emit only when the state changes.** An
  exception after `handoff_to` is logged but no longer emits `agent.failed`.
- **A `TimeoutError` raised by your own code stays a `TimeoutError`.** Only the
  session's deadline becomes `TurnTimeoutError`.

## Streaming

- `timeout` covers waiting on upstream, and can no longer cancel the consumer's task.
- A limit crossed after the stream ends now yields `error` + `final("failed")`.
- A stream that ends early books its usage if `usage` or `estimate_usage` provides it.

## Reliability primitives

- **Circuit breaker:** a probe silent for longer than `recovery_timeout_seconds` is
  abandoned and its late result ignored. `half_open_max_probes` now limits probes per
  cycle, not just at once. `record_success()` closes a circuit that reports HALF_OPEN.
- **Rate limiter:** `acquire_usage` no longer rejects an amount above capacity,
  because it has already been spent. The balance goes negative and the call waits for
  it to refill, which can block for longer when `timeout=None`.
- **Failure classification:** digits inside identifiers such as `req_84010` no longer
  count as HTTP status codes, and context-overflow messages win over a stray `429`.
- **Checkpoints:** `get` and `list` order by insertion rather than timestamp, so a
  clock step can no longer hide the latest checkpoint.

## Pricing and output parsing

- **Pricing prefixes need a separator.** `model-a` still prices `model-a-20260514`,
  but no longer prices `model-alpha`, which now costs $0 and logs the usual warning.
  Register such models explicitly.
- `UsageLimitExceededError` for `cost_usd` reports dollars rather than micro-dollars.
- A cache write on a model with only 5m and 1h tiers is charged at the higher tier
  rather than $0.
- `JsonOutputSpec` rejects `NaN` and `Infinity`, and rejects `true` for
  `require_type=int`.
- Compaction always keeps the newest message, even if that exceeds the limit, and the
  kept window never starts on a `tool` message.

## Guards and compliance

- **Role names are normalized** on both sides of a permission check. Two role configs
  differing only by case now raise `ValueError`.
- `evaluate()` raises `TypeError` for an async policy condition rather than treating
  it as true. Use `evaluate_async`.
- **Human oversight rejects** a repeated `decision_id`, a stale or future decision, a
  queue posing as the system reviewer, and an unknown outcome. The gate enforces its
  own timeout on a queue that hangs.
- `verify_chain` on a live sink detects a deleted tail. See the
  [audit log guide](eu-ai-act/audit-log.md) for `tip()`, `expected_tip`, and the
  limit after a restart.
- `purge_expired` refuses a naive `now` and only deletes a contiguous oldest prefix.
- The conformity self-check reports Article 12 as not met for an in-memory log, or
  when the purge floor is under 183 days.

## Telemetry

- The agent span is named `invoke_agent` from the start, not `create_agent`, and
  nests under its phase span. Update trace filters that match on the old name.
- `OpenTelemetryUsageSink` also emits the `gen_ai.client.inference.usage.*` counters.
  `gen_ai.client.token.usage` is no longer in the semantic conventions; it stays on
  for now and `emit_legacy_token_metric=False` turns it off.
- New `OpenTelemetryMetricsSink` records `gen_ai.client.inference.duration` and
  `gen_ai.execute_tool.duration`.

## MCP

The `[mcp]` extra now accepts `mcp>=1.10,<3`. The floor moved because versions before
1.10 lack `structuredContent`. `MCPToolAdapter` accepts a 1.x or 2.x `ClientSession`
or a 2.x `Client`, and `tool()` takes a `timeout`.
