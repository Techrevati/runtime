# Durable execution on Redis

For low-latency, shared checkpoint storage, implement `CheckpointSaver` against
Redis. Install the deps:

```bash
pip install 'techrevati-runtime[redis]'
```

This is a **recipe**, not a shipped module. Redis is best for short-to-medium
retention and fast cross-process resume; for long-term audit retention prefer a
relational store (see [PostgreSQL](durability-postgres.md)).

```python
import json
from datetime import UTC, datetime
from uuid import uuid4

import redis
from techrevati.runtime import Checkpoint


class RedisSaver:  # satisfies CheckpointSaver structurally
    def __init__(self, client: redis.Redis, *, ttl_seconds: int | None = None) -> None:
        self._r = client
        self._ttl = ttl_seconds

    def put(self, thread_id, state, *, parent_id=None, metadata=None):
        cp = Checkpoint(
            id=uuid4().hex, thread_id=thread_id,
            created_at=datetime.now(UTC).isoformat(),
            state=dict(state), parent_id=parent_id, metadata=dict(metadata or {}),
        )
        key = f"ckpt:{thread_id}"
        # Sorted set by created_at for ordered listing; payloads in a hash.
        self._r.hset(f"{key}:payloads", cp.id, json.dumps(cp.to_dict()))
        self._r.zadd(key, {cp.id: _score(cp.created_at)})
        if self._ttl:
            self._r.expire(key, self._ttl)
            self._r.expire(f"{key}:payloads", self._ttl)
        return cp

    def get(self, thread_id, checkpoint_id=None):
        key = f"ckpt:{thread_id}"
        if checkpoint_id is None:
            ids = self._r.zrevrange(key, 0, 0)
            if not ids:
                return None
            checkpoint_id = ids[0]
        raw = self._r.hget(f"{key}:payloads", checkpoint_id)
        return None if raw is None else Checkpoint.from_dict(json.loads(raw))

    def list(self, thread_id, *, before=None, limit=10): ...   # zrevrange + keyset
    def delete(self, thread_id):
        self._r.delete(f"ckpt:{thread_id}", f"ckpt:{thread_id}:payloads")


def _score(created_at: str) -> float:
    return datetime.fromisoformat(created_at).timestamp()
```

Inject the client (`RedisSaver(redis.Redis(...))`) so you can unit-test against
[`fakeredis`](https://pypi.org/project/fakeredis/) without a live server, then
wire it via `AgentSession(saver=RedisSaver(...))`.
