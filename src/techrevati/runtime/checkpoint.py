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

The ``CheckpointSaver`` protocol exposes a small
``get`` / ``put`` / ``list`` / ``delete`` shape so adapters can bridge it to
external checkpoint stores without adding runtime dependencies.

Caveat: this is restart-resumable execution, not full workflow history.
Step-level replay (re-running a half-finished turn against a recorded history)
is NOT in scope; checkpoints only fire between turns. Use an external
coordinator when you need cross-host scheduling or durable timers.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from contextlib import suppress
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


_SQLITE_COMPONENT = "checkpoint"
_SQLITE_SCHEMA_VERSION = 1


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validate_non_empty_str("id", self.id))
        object.__setattr__(
            self, "thread_id", _validate_non_empty_str("thread_id", self.thread_id)
        )
        object.__setattr__(
            self, "created_at", _validate_non_empty_str("created_at", self.created_at)
        )
        object.__setattr__(
            self,
            "parent_id",
            _validate_optional_non_empty_str("parent_id", self.parent_id),
        )
        object.__setattr__(self, "state", _normalize_json_mapping("state", self.state))
        object.__setattr__(
            self, "metadata", _normalize_json_mapping("metadata", self.metadata)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "state": _normalize_json_mapping("state", self.state),
            "parent_id": self.parent_id,
            "metadata": _normalize_json_mapping("metadata", self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Checkpoint:
        if not isinstance(data, Mapping):
            raise TypeError("data must be a mapping")
        state = data.get("state", {})
        metadata = data.get("metadata", {})
        if state is None:
            state = {}
        if metadata is None:
            metadata = {}
        if not isinstance(state, Mapping):
            raise TypeError("state must be a mapping")
        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        return cls(
            id=data["id"],
            thread_id=data["thread_id"],
            created_at=data["created_at"],
            state=dict(state),
            parent_id=data.get("parent_id"),
            metadata=dict(metadata),
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


def _validate_non_empty_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_optional_non_empty_str(field_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _validate_non_empty_str(field_name, value)


def _normalize_json_mapping(
    field_name: str, value: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    copied = dict(value)
    for key in copied:
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
    try:
        encoded = json.dumps(copied, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be JSON-serializable") from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError(f"{field_name} must encode to a JSON object")
    return decoded


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    return limit


def _copy_checkpoint(checkpoint: Checkpoint) -> Checkpoint:
    return Checkpoint.from_dict(checkpoint.to_dict())


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
        thread_id = _validate_non_empty_str("thread_id", thread_id)
        checkpoint_id = _validate_optional_non_empty_str("checkpoint_id", checkpoint_id)
        with self._lock:
            entries = self._threads.get(thread_id)
            if not entries:
                return None
            if checkpoint_id is None:
                return _copy_checkpoint(entries[-1])
            for cp in reversed(entries):
                if cp.id == checkpoint_id:
                    return _copy_checkpoint(cp)
            return None

    def put(
        self,
        thread_id: str,
        state: Mapping[str, Any],
        *,
        parent_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Checkpoint:
        thread_id = _validate_non_empty_str("thread_id", thread_id)
        cp = Checkpoint(
            id=_new_id(),
            thread_id=thread_id,
            created_at=_now_iso(),
            state=_normalize_json_mapping("state", state),
            parent_id=parent_id,
            metadata=_normalize_json_mapping("metadata", metadata or {}),
        )
        with self._lock:
            self._threads.setdefault(thread_id, []).append(_copy_checkpoint(cp))
        return _copy_checkpoint(cp)

    def list(
        self, thread_id: str, *, before: str | None = None, limit: int = 10
    ) -> list[Checkpoint]:
        thread_id = _validate_non_empty_str("thread_id", thread_id)
        before = _validate_optional_non_empty_str("before", before)
        limit = _validate_limit(limit)
        if limit <= 0:
            return []
        with self._lock:
            entries = list(self._threads.get(thread_id, ()))
        if before is not None:
            cursor = next((cp for cp in entries if cp.id == before), None)
            if cursor is None:
                return []
            # Keyset filter on the SAME total order SqliteSaver uses
            # ``(created_at, id)`` so the two savers stay interchangeable even
            # when checkpoints share a ``created_at`` (insertion order would
            # diverge from sqlite's id tiebreaker otherwise).
            cutoff = (cursor.created_at, cursor.id)
            entries = [cp for cp in entries if (cp.created_at, cp.id) < cutoff]
        # Newest first: (created_at DESC, id DESC), matching SqliteSaver.
        ordered = sorted(entries, key=lambda cp: (cp.created_at, cp.id), reverse=True)
        return [_copy_checkpoint(cp) for cp in ordered[:limit]]

    def delete(self, thread_id: str) -> None:
        thread_id = _validate_non_empty_str("thread_id", thread_id)
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

_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS techrevati_runtime_metadata (
    component      TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL
);
"""


def _ensure_schema_version(
    conn: sqlite3.Connection, *, component: str, version: int
) -> None:
    conn.executescript(_METADATA_SCHEMA)
    row = conn.execute(
        "SELECT schema_version FROM techrevati_runtime_metadata WHERE component = ?",
        (component,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO techrevati_runtime_metadata"
            " (component, schema_version) VALUES (?, ?)",
            (component, version),
        )
        return
    observed = int(row[0])
    if observed != version:
        raise RuntimeError(
            f"unsupported sqlite schema for {component}: "
            f"version {observed}, expected {version}"
        )


def _open_wal(path: str | Path) -> sqlite3.Connection:
    """Open a sqlite connection in WAL mode before any schema mutations."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        # WAL is a no-op for ":memory:"; harmless to request.
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        with suppress(Exception):
            conn.close()
        raise
    return conn


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
        self._conn = _open_wal(self._path)
        self._lock = threading.Lock()
        try:
            with self._lock:
                _ensure_schema_version(
                    self._conn,
                    component=_SQLITE_COMPONENT,
                    version=_SQLITE_SCHEMA_VERSION,
                )
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
        except Exception:
            self._conn.close()
            raise

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
        thread_id = _validate_non_empty_str("thread_id", thread_id)
        checkpoint_id = _validate_optional_non_empty_str("checkpoint_id", checkpoint_id)
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
        thread_id = _validate_non_empty_str("thread_id", thread_id)
        cp = Checkpoint(
            id=_new_id(),
            thread_id=thread_id,
            created_at=_now_iso(),
            state=_normalize_json_mapping("state", state),
            parent_id=parent_id,
            metadata=_normalize_json_mapping("metadata", metadata or {}),
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
        thread_id = _validate_non_empty_str("thread_id", thread_id)
        before = _validate_optional_non_empty_str("before", before)
        limit = _validate_limit(limit)
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
                    "SELECT created_at, id FROM checkpoints"
                    " WHERE thread_id = ? AND id = ? LIMIT 1",
                    (thread_id, before),
                ).fetchone()
                if ts_row is None:
                    return []
                rows = self._conn.execute(
                    "SELECT id, thread_id, created_at, state_json, parent_id,"
                    " metadata_json FROM checkpoints"
                    " WHERE thread_id = ?"
                    " AND (created_at < ? OR (created_at = ? AND id < ?))"
                    " ORDER BY created_at DESC, id DESC LIMIT ?",
                    (thread_id, ts_row[0], ts_row[0], ts_row[1], limit),
                ).fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    def delete(self, thread_id: str) -> None:
        thread_id = _validate_non_empty_str("thread_id", thread_id)
        with self._lock:
            self._conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,)
            )
            self._conn.commit()
