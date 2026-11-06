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
| P2 | Replay scan depth | Turn replay looks at the latest 100 checkpoints of a thread. |
| P2 | PostgreSQL recipe | [The recipe](patterns/durability-postgres.md) still orders by `created_at`, so a clock step can reorder it. |
| P2 | Release-candidate guards | The `production-rc-*` guards still measure the 0.3.0 snapshot, so they only run on those branches. |
