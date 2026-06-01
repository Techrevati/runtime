# Durable execution — CheckpointSaver

`CheckpointSaver` persists per-turn snapshots of a session so a crashed
or paused agent loop can resume from the last committed turn instead of
re-running every step. Pair it with a stable `thread_id` and the
`idempotency_key` argument to `run_turn` / `arun_turn` for replay-safe
multi-turn loops.

## Quick example

```python
from techrevati.runtime import (
    AgentSession, SqliteSaver, UsageSnapshot,
)

saver = SqliteSaver("checkpoints.db")
agent = AgentSession(role="writer", phase="draft", saver=saver)

with agent.session(thread_id="user-42:essay") as session:
    draft, usage = session.run_turn(
        lambda: call_model(outline_prompt),
        model="model-a",
        usage=UsageSnapshot(input_tokens=2_000, output_tokens=900),
        idempotency_key="draft:turn-1",
    )
    revision, _ = session.run_turn(
        lambda: call_model(revision_prompt(draft)),
        model="model-a",
        idempotency_key="draft:turn-2",
    )
```

On a clean run, two checkpoints land in `checkpoints.db`. If the
process crashes between the two turns and a future invocation opens a
fresh `AgentSession` against the same `thread_id`, the first
`run_turn` returns the cached result for `"draft:turn-1"` without
calling the model again, and execution continues with turn 2.

## When to use

- Long agent loops where a single retry costs real money / latency.
- Multi-stage pipelines that you want to resume mid-flight after a
  pod restart, deploy, or transient failure.
- Idempotent webhook handlers — the `idempotency_key` is exactly the
  request id you'd use to dedupe.

## When NOT to use

- The whole loop is cheap and re-running from scratch is fine.
- Results aren't JSON-serializable and you can't coerce them. The saver
  logs a warning and skips the checkpoint, so the call still works but
  the durability guarantee is lost.
- You need step-level replay (run a half-finished turn against
  a recorded history). This module checkpoints between turns, not inside
  one. Wrap a durable engine behind a custom `CheckpointSaver` impl if
  you need that semantic.

## Reference implementations

- `InMemorySaver` — process-local, lost on exit, thread-safe. Default
  for tests and dev loops.
- `SqliteSaver(path)` — stdlib `sqlite3` only, no new runtime
  dependency, WAL mode for concurrent readers. Pass `":memory:"` for a
  fully in-memory database scoped to one connection. The saver records
  its supported schema version in `techrevati_runtime_metadata` and
  refuses to open a database marked with an unsupported version.

Both implement the same `CheckpointSaver` protocol, so a session can
swap between them by changing the `saver=` argument on `AgentSession`.

## Anti-patterns

- **Reusing one `thread_id` across unrelated sessions.** The `list`
  returned by the saver is a single log; mixing logs means
  `idempotency_key` collisions can resurrect the wrong result.
  Namespace your thread ids (`user-42:essay`, not `essay`).
- **Treating `idempotency_key` as a cache key.** It's a replay marker
  scoped to one thread. Two threads with the same key get independent
  results.
- **Mutating the dict you pass to `put`.** Savers copy on insert, but
  test helpers that re-use a dict between turns will surprise you if
  you check identity. Build a fresh dict per turn.

## Tuning

| Knob | Default | Why touch it |
|---|---|---|
| `SqliteSaver` `path` | required | `:memory:` for tests; a real file for restart durability. |
| `list(..., limit=N)` | 10 | Raise it if you have very long threads and need to reach further back. |
| `_restore_idempotent_turn` scan depth | 100 | Internal cap on how far back an idempotency lookup walks. If your threads exceed 100 turns and you need replay older than that, cache the lookup outside the runtime. |
| SQLite schema version | 1 | Managed by the runtime; create a fresh database or migrate explicitly if a future version changes the schema. |

## See also

- [Migrating from 0.0.x](../migrating-from-0.0.x.md) — `thread_id` /
  `idempotency_key` are new in 0.2.0; older sessions keep working
  without them.
- [Orchestrator](orchestrator.md) — how the saver is wired into the
  session lifecycle.
