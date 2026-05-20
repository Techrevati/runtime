"""
Checkpoint — Durable execution primitives for restart-resumable agent sessions.

A ``CheckpointSaver`` persists per-turn snapshots of a session so that a
crashed or paused agent loop can resume from the last committed turn
instead of re-running every step from the beginning. Two reference
implementations ship in this module:

- ``InMemorySaver`` — fast, process-local, lost on exit. Default when
  a thread_id is supplied without an explicit saver.
- ``SqliteSaver`` — durable across process restarts, uses stdlib
  ``sqlite3`` only (no new runtime dependency), WAL mode for concurrent
  readers + one writer.

The ``CheckpointSaver`` protocol mirrors the LangGraph
``get`` / ``put`` / ``list`` / ``delete`` shape so downstream code that
already knows that contract reads naturally here.

Caveat: this is restart-resumable execution, not Temporal-style durable
execution. Step-level replay (re-running a half-finished turn against a
recorded history) is NOT in scope; checkpoints only fire between turns.
For workflow-engine semantics, pair this with Temporal / Restate / DBOS
behind a wrapping ``CheckpointSaver`` implementation. See
``docs/patterns/durability.md`` for the trade-off.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Checkpoint",
    "CheckpointSaver",
    "InMemorySaver",
    "SqliteSaver",
]


@dataclass(frozen=True)
class Checkpoint:
    """A point-in-time snapshot of a session's serializable state.

    ``state`` and ``metadata`` must be JSON-serializable end to end. The
    saver is allowed to round-trip them through ``json.dumps`` and
    expects to get a structurally equal mapping back; non-serializable
    values (callables, sockets, dataclass instances that don't define
    ``to_dict``) must be coerced by the caller before ``put``.
    """

    id: str
    thread_id: str
    created_at: str  # ISO 8601 UTC
    state: dict[str, Any]
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "state": dict(self.state),
            "parent_id": self.parent_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Checkpoint:
        return cls(
            id=data["id"],
            thread_id=data["thread_id"],
            created_at=data["created_at"],
            state=dict(data.get("state") or {}),
            parent_id=data.get("parent_id"),
            metadata=dict(data.get("metadata") or {}),
        )


@runtime_checkable
class CheckpointSaver(Protocol):
    """Persistence contract for session checkpoints.

    Implementations should be safe for concurrent reads; writes may be
    serialized internally (the in-memory and sqlite reference impls
    both serialize writes under a lock).
    """

    def get(
        self, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        """Return the requested checkpoint, or the latest if id is None."""
        ...

    def put(
        self,
        thread_id: str,
        state: Mapping[str, Any],
        *,
        parent_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        """Persist a new checkpoint. Returns the materialized record."""
        ...

    def list(
        self, thread_id: str, *, before: str | None = None, limit: int = 10
    ) -> list[Checkpoint]:
        """Return checkpoints for the thread, newest first.

        If ``before`` is given (a checkpoint id), only checkpoints
        created strictly earlier are returned. ``limit`` is treated as
        a hard cap.
        """
        ...

    def delete(self, thread_id: str) -> None:
        """Remove every checkpoint for the given thread."""
        ...


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class InMemorySaver:
    """Process-local checkpoint store. Lost on exit; thread-safe.

    Useful for tests, dev loops, and any session that does not need to
    survive a process restart.
    """

    def __init__(self) -> None:
        self._threads: dict[str, list[Checkpoint]] = {}
        self._lock = threading.Lock()

    def get(
        self, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        with self._lock:
            entries = self._threads.get(thread_id)
            if not entries:
                return None
            if checkpoint_id is None:
                return entries[-1]
            for cp in reversed(entries):
                if cp.id == checkpoint_id:
                    return cp
            return None

    def put(
        self,
        thread_id: str,
        state: Mapping[str, Any],
        *,
        parent_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        cp = Checkpoint(
            id=_new_id(),
            thread_id=thread_id,
            created_at=_now_iso(),
            state=dict(state),
            parent_id=parent_id,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._threads.setdefault(thread_id, []).append(cp)
        return cp

    def list(
        self, thread_id: str, *, before: str | None = None, limit: int = 10
    ) -> list[Checkpoint]:
        if limit <= 0:
            return []
        with self._lock:
            entries = list(self._threads.get(thread_id, ()))
        if before is not None:
            cutoff_idx: int | None = None
            for idx, cp in enumerate(entries):
                if cp.id == before:
                    cutoff_idx = idx
                    break
            entries = entries[:cutoff_idx] if cutoff_idx is not None else []
        return list(reversed(entries))[:limit]

    def delete(self, thread_id: str) -> None:
        with self._lock:
            self._threads.pop(thread_id, None)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    state_json  TEXT NOT NULL,
    parent_id   TEXT,
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_created
    ON checkpoints(thread_id, created_at DESC);
"""


class SqliteSaver:
    """Stdlib-sqlite3 checkpoint store. Survives process restart.

    Uses WAL mode so concurrent readers don't block the writer. Writes
    are serialized inside a single connection per saver instance (the
    standard sqlite3 recommendation: one connection per writer). All
    state and metadata payloads are JSON-encoded.

    Pass ``":memory:"`` as the path for a fully in-memory database;
    that variant is reset on garbage collection (use ``InMemorySaver``
    for tests that don't need the sqlite execution path under coverage).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # check_same_thread=False so a session running on a worker thread
        # can still reuse the saver created on the main thread; we
        # serialize all writes through ``_lock`` to keep that safe.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # WAL is a no-op for ":memory:"; harmless to request.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteSaver:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @staticmethod
    def _row_to_checkpoint(row: tuple[Any, ...]) -> Checkpoint:
        return Checkpoint(
            id=row[0],
            thread_id=row[1],
            created_at=row[2],
            state=json.loads(row[3]),
            parent_id=row[4],
            metadata=json.loads(row[5]),
        )

    def get(
        self, thread_id: str, checkpoint_id: str | None = None
    ) -> Checkpoint | None:
        with self._lock:
            if checkpoint_id is None:
                row = self._conn.execute(
                    "SELECT id, thread_id, created_at, state_json, parent_id,"
                    " metadata_json FROM checkpoints WHERE thread_id = ?"
                    " ORDER BY created_at DESC, id DESC LIMIT 1",
                    (thread_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT id, thread_id, created_at, state_json, parent_id,"
                    " metadata_json FROM checkpoints"
                    " WHERE thread_id = ? AND id = ? LIMIT 1",
                    (thread_id, checkpoint_id),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_checkpoint(row)

    def put(
        self,
        thread_id: str,
        state: Mapping[str, Any],
        *,
        parent_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        cp = Checkpoint(
            id=_new_id(),
            thread_id=thread_id,
            created_at=_now_iso(),
            state=dict(state),
            parent_id=parent_id,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO checkpoints"
                " (id, thread_id, created_at, state_json, parent_id, metadata_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cp.id,
                    cp.thread_id,
                    cp.created_at,
                    json.dumps(cp.state, ensure_ascii=False),
                    cp.parent_id,
                    json.dumps(cp.metadata, ensure_ascii=False),
                ),
            )
            self._conn.commit()
        return cp

    def list(
        self, thread_id: str, *, before: str | None = None, limit: int = 10
    ) -> list[Checkpoint]:
        if limit <= 0:
            return []
        with self._lock:
            if before is None:
                rows = self._conn.execute(
                    "SELECT id, thread_id, created_at, state_json, parent_id,"
                    " metadata_json FROM checkpoints WHERE thread_id = ?"
                    " ORDER BY created_at DESC, id DESC LIMIT ?",
                    (thread_id, limit),
                ).fetchall()
            else:
                # Resolve ``before`` to its created_at so we can compare
                # without depending on UUID ordering.
                ts_row = self._conn.execute(
                    "SELECT created_at FROM checkpoints"
                    " WHERE thread_id = ? AND id = ? LIMIT 1",
                    (thread_id, before),
                ).fetchone()
                if ts_row is None:
                    return []
                rows = self._conn.execute(
                    "SELECT id, thread_id, created_at, state_json, parent_id,"
                    " metadata_json FROM checkpoints"
                    " WHERE thread_id = ? AND created_at < ?"
                    " ORDER BY created_at DESC, id DESC LIMIT ?",
                    (thread_id, ts_row[0], limit),
                ).fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    def delete(self, thread_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,)
            )
            self._conn.commit()
