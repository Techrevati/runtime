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
- **Hard limits are checked before a turn too.** With `enforce_budget=True` or
  `usage_limits`, a session already over its limit raises before calling the model. Code
  that caught `BudgetExceededError` and carried on now gets it again before paying.
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
- A stream that ends early books its usage if `usage` or `estimate_usage` provides it
  and at least one chunk arrived.
- Leaving a stream on purpose no longer counts as a failure, and its span and metric
  carry no `error.type`.
- A chunk that is not a `str` raises `TypeError` through the terminal events.

## Reliability primitives

- **Circuit breaker:** a probe silent for longer than `recovery_timeout_seconds` is
  abandoned, and the result of any call admitted before the circuit last opened is
  ignored. A success from an abandoned probe can still close the circuit, so a downstream
  slower than the recovery window can recover; one new probe per window may run above
  `half_open_max_probes` until then. `half_open_max_probes` now limits probes per
  cycle, not just at once. `record_success()` closes a circuit that reports HALF_OPEN.
- **Rate limiter:** `acquire_usage` no longer rejects an amount above capacity,
  because it has already been spent. The balance goes negative and the call waits for
  it to refill, which can block for longer when `timeout=None`. Debt is capped at 24
  hours of refill, and amounts above `1e15` raise `ValueError`.
- **Failure classification:** digits inside identifiers such as `req_84010` no longer
  count as HTTP status codes, and context-overflow messages win over a stray `429`.
  `prompt is too long` is a context overflow, and `Too many tokens, please wait` is a
  rate limit.
- **Checkpoints:** `get` and `list` order by insertion rather than timestamp, so a
  clock step can no longer hide the latest checkpoint. A lone surrogate in a thread id
  or step key raises `ValueError`, on `InMemorySaver` too.

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

- **A role that only resembles a configured role is denied.** With `admin` configured,
  `ADMIN` or `admin` plus an invisible character is refused whatever
  `default_allow_unknown_roles` says. Exact matches behave as before, and `writer` and
  `WRITER` may be configured separately.
- `tool_requirements` keys that collide after folding, and tool names that fold to
  nothing, raise `ValueError` at construction.
- A guardrail a sync session cannot enforce is refused before the tool runs, rather
  than skipping the check. A fully async guardrail is still skipped with a warning. A
  guardrail with no post check passes that stage, and one with no checks raises
  `TypeError`.
- Role configs whose names differ only by surrounding whitespace raise `ValueError`.
- `evaluate()` raises `TypeError` for an async policy condition rather than treating
  it as true. Use `evaluate_async`.
- **Human oversight rejects** a repeated `decision_id`, a stale or future decision, a
  queue posing as the system reviewer, and an unknown outcome. The gate enforces its
  own timeout on a queue that hangs.
- `verify_chain` on a live sink detects a deleted tail. See the
  [audit log guide](eu-ai-act/audit-log.md) for `tip()`, `expected_tip`, and the
  limit after a restart.
- `purge_expired` refuses a naive `now` and only deletes a contiguous oldest prefix. It
  records a marker in a new `audit_log_purge_marker` table, and the chain continues
  after a full purge rather than restarting from genesis.
- `SqliteAuditBackend` waits up to `busy_timeout_seconds=30` for a lock (about 5 before),
  so a write can block longer under heavy contention. Lower it for event-loop callers.
- Audit payloads are cut at depth 100 or 100,000 nodes, with a marker, rather than
  dropping the event. A value that cannot be represented is replaced on its own.
- `InputSanitizationHook` and `OutputIntegrityGuardrail` no longer reject binary such as
  `b"\x00\x00\x00\x01"`, scan text-like bytes for escape sequences, and walk mappings
  and re-iterable containers.
- With a signing key, `verify_chain` reports a purge marker with a missing or wrong
  signature as invalid, including one left by a direct `backend.purge_before` call or a
  sink without the key.
- Sets and frozensets in event data are recorded as lists, and tuple keys as JSON.
- A `decision_id` with surrounding whitespace raises `ValueError`.
- Redacted dict keys that collide get a suffix (`[REDACTED]#2`) rather than losing an
  entry.
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
