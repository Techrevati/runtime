# Durable execution on PostgreSQL

The runtime core is zero-dependency, so it ships SQLite reference savers only. For
fleet-wide retention, implement the `CheckpointSaver` (and, for the EU AI Act
audit log, `AuditBackend`) protocols against your own database. Install the deps:

```bash
pip install 'techrevati-runtime[postgres]'      # psycopg 3
```

This is a **recipe**, not a shipped module — you own the connection pool, schema
migrations, and retention. The protocols are intentionally small so an adapter is
a single file.

## CheckpointSaver over psycopg

```python
import json
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
from techrevati.runtime import Checkpoint, CheckpointSaver  # Protocol


class PostgresSaver:  # satisfies CheckpointSaver structurally
    def __init__(self, conninfo: str) -> None:
        self._conn = psycopg.connect(conninfo, autocommit=True)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints ("
            " seq BIGINT GENERATED ALWAYS AS IDENTITY,"
            " id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created_at TEXT NOT NULL,"
            " state_json JSONB NOT NULL, parent_id TEXT, metadata_json JSONB NOT NULL)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ckpt_thread_seq"
            " ON checkpoints (thread_id, seq DESC)"
        )

    def put(self, thread_id, state, *, parent_id=None, metadata=None):
        cp = Checkpoint(
            id=uuid4().hex, thread_id=thread_id,
            created_at=datetime.now(UTC).isoformat(),
            state=dict(state), parent_id=parent_id, metadata=dict(metadata or {}),
        )
        self._conn.execute(
            "INSERT INTO checkpoints"
            " (id,thread_id,created_at,state_json,parent_id,metadata_json)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (cp.id, cp.thread_id, cp.created_at, json.dumps(cp.state),
             cp.parent_id, json.dumps(cp.metadata)),
        )
        return cp

    def get(self, thread_id, checkpoint_id=None):
        if checkpoint_id is None:
            row = self._conn.execute(
                "SELECT id,thread_id,created_at,state_json,parent_id,metadata_json"
                " FROM checkpoints WHERE thread_id=%s ORDER BY seq DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id,thread_id,created_at,state_json,parent_id,metadata_json"
                " FROM checkpoints WHERE thread_id=%s AND id=%s",
                (thread_id, checkpoint_id),
            ).fetchone()
        return None if row is None else Checkpoint(
            id=row[0], thread_id=row[1], created_at=row[2],
            state=row[3], parent_id=row[4], metadata=row[5],
        )

    def list(self, thread_id, *, before=None, limit=10): ...   # keyset by seq DESC
    def delete(self, thread_id): ...
```

`seq` keeps insertion order, like the SQLite saver's rowid: `created_at` is a wall
clock and can step backwards. Identity values are assigned at insert, so two
transactions writing the *same* thread at once can commit out of `seq` order; a
session writes its thread serially, so this only matters if you share a thread id
across writers. `CREATE TABLE IF NOT EXISTS` will not add `seq` to an existing
table; migrate that table first.

Wire it exactly like the SQLite saver: `AgentSession(saver=PostgresSaver(...))` with
a `thread_id` at `session()` time.

## AuditBackend over psycopg (Article 12)

The EU AI Act `AuditLogSink` accepts any `AuditBackend`. A Postgres backend that
gives the audit user **append-only** privileges (no `UPDATE`/`DELETE` grant) turns
the hash chain's tamper-*evidence* into stronger tamper-*resistance*:

```python
from techrevati.runtime.compliance import AuditLogSink, AuditRecord

class PostgresAuditBackend:  # satisfies AuditBackend structurally
    def append(self, record: AuditRecord) -> None: ...   # INSERT only
    def last(self): ...
    def records(self): ...
    def count(self): ...
    def purge_before(self, cutoff: str) -> int: ...

sink = AuditLogSink(PostgresAuditBackend(...), signing_key=hsm_key)
```

See [Audit Log](../eu-ai-act/audit-log.md) for the chain semantics and threat model.
