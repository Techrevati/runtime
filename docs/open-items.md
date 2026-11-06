# Open items

Known limitations and pending decisions, so they are not rediscovered. Items leave
this page when they are fixed or rejected, with the reason recorded in the changelog.

## Decisions for the maintainer

| Item | Current behaviour | Recommendation |
|---|---|---|
| Unknown roles | `default_allow_unknown_roles=True`: a role with no config is allowed. | Default to `False` in the next minor. It is a breaking change; the pilot profile already does this. |
| Timeouts and the circuit breaker | A turn timeout does not count as a breaker failure, so a provider that hangs never opens it. | Count timeouts as failures, as most breaker implementations do. |
| Fail-closed audit | A failed audit write is reported and the tool still runs. | Add an opt-in session mode that refuses the action when it cannot be recorded. |

## Known limitations

| Priority | Item | Detail |
|---|---|---|
| P2 | Large `acquire` can starve | A large request waits behind a stream of small ones. A fair queue would change what `available` and `try_acquire` mean. See [rate limiting](patterns/rate-limiting.md). |
| P2 | Sanitization needs arguments | `InputSanitizationHook` only sees tool arguments passed through `args=` or `hook_ctx`, while the Article 15 self-check item does not reflect that. |
| P2 | Audit truncation after a restart | A restarted sink cannot detect a deleted tail without an externally stored `expected_tip`. See the [audit log guide](eu-ai-act/audit-log.md). |
| P2 | Purge markers without a key | Without a signing key a purge marker proves nothing to someone who can write to the database, and it weakens `expected_tip`. Use a signing key where that matters. |
| P2 | Event data copying | `AgentEvent.with_data` is slow on deeply nested tuples and frozensets, and rejects non-string keys, before a sink is reached. |
| P2 | Slow `__repr__` | Audit coercion contains exceptions from an object's `__repr__`, but not the time it takes. |
| P2 | Oversight replay window | Replay protection is per process and covers the latest 65,536 ids. A retry after a failed audit write may get a different answer from the review channel; the log shows both. |
| P2 | Homoglyphs in tool names | A Cyrillic letter inside `delete` is a different name, so a deny list misses it. Use `allowed_tools` for tools that matter. |
| P2 | Spaced status codes | A bare number counts as a status, so `took 401 ms` classifies as a provider failure, while `401ms` does not. |
| P2 | Replay scan depth | Turn replay looks at the latest 100 checkpoints of a thread. |
| P2 | Redis recipe | [The recipe](patterns/durability-redis.md) still scores by `created_at`, so a clock step can reorder it. The PostgreSQL recipe's new ordering has not been run against a real server. |
| P2 | Recovery events | `recovery.succeeded` means the recipe ran, not that the call worked, so it also appears for a failed attempt. |
| P2 | MCP closed session | On SDK 1.x a closed session raises the transport's `ClosedResourceError` rather than `MCPToolError`. |
| P2 | Binary scanning | An escape sequence hidden in mostly unreadable bytes past the first 64 is not detected, because scanning all binary would reject ordinary files. |
| P2 | Audit lock wait | `busy_timeout_seconds` blocks the caller, and so the event loop for an async session, while waiting. |
| P2 | Release-candidate guards | The `production-rc-*` guards still measure the 0.3.0 snapshot, so they only run on those branches. |
