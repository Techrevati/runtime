"""
Persistence — SQLite-backed sinks for long-lived sessions.

``RingBufferEventSink`` / ``RingBufferUsageSink`` are great for short
sessions and tests, but their in-memory deques cap out at
``DEFAULT_RING_CAPACITY`` events. A session that runs for hours and
emits thousands of events silently drops the oldest entries.

``SqliteEventSink`` and ``SqliteUsageSink`` persist to a stdlib
``sqlite3`` database — same zero-dependency constraint as
``SqliteSaver`` — with WAL mode so concurrent readers don't block the
writer. Use them when you need an event log that survives a process
restart, or when the in-memory buffer would otherwise overflow.

Both sinks are write-ahead: ``emit`` / ``record`` return after the
INSERT commits. For throughput-critical scenarios, wrap them in a
bounded queue + background-thread flusher; the protocols are kept
small so adapters are easy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from techrevati.runtime._internal import (
    _ensure_schema_version,
    _validate_cost_usd,
    _validate_model,
)
from techrevati.runtime.agent_events import AgentEvent
from techrevati.runtime.usage_tracking import UsageSnapshot

__all__ = [
    "SqliteEventSink",
    "SqliteUsageSink",
]


_EVENT_SINK_COMPONENT = "event_sink"
_USAGE_SINK_COMPONENT = "usage_sink"
_SQLITE_SCHEMA_VERSION = 1


def _validate_event(event: AgentEvent) -> AgentEvent:
    if not isinstance(event, AgentEvent):
        raise TypeError("event must be an AgentEvent")
    return event


def _validate_usage(usage: UsageSnapshot) -> UsageSnapshot:
    if not isinstance(usage, UsageSnapshot):
        raise TypeError("usage must be a UsageSnapshot")
    return usage


def _validate_optional_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit must be an integer or None")
    return limit


_EVENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    emitted_at  TEXT NOT NULL,
    event       TEXT NOT NULL,
    role        TEXT,
    phase       TEXT,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_emitted_at
    ON agent_events(emitted_at);
"""

_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    model       TEXT NOT NULL,
    cost_usd    REAL NOT NULL,
    snapshot    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_records_recorded_at
    ON usage_records(recorded_at);
"""


def _open_wal(path: str | Path) -> sqlite3.Connection:
    """Open a sqlite connection in WAL mode (no-op for ``:memory:``)."""
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        with suppress(Exception):
            conn.close()
        raise
    return conn


@dataclass
class SqliteEventSink:
    """EventSink that persists every event to a sqlite table.

    Pass ``:memory:`` as path for a short-lived in-process store
    (still durable for the lifetime of the process). For real
    durability, pass an on-disk path.
    """

    path: str | Path
    _conn: sqlite3.Connection = field(init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._conn = _open_wal(self.path)
        try:
            with self._lock:
                _ensure_schema_version(
                    self._conn,
                    component=_EVENT_SINK_COMPONENT,
                    version=_SQLITE_SCHEMA_VERSION,
                )
                self._conn.executescript(_EVENT_SCHEMA)
                self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    def emit(self, event: AgentEvent) -> None:
        event = _validate_event(event)
        payload = json.dumps(event.to_dict(), ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_events (emitted_at, event, role, phase, payload)"
                " VALUES (?, ?, ?, ?, ?)",
                (event.emitted_at, event.event.value, event.role, event.phase, payload),
            )
            self._conn.commit()

    def replay(self, *, limit: int | None = None) -> Iterator[AgentEvent]:
        """Yield every persisted event in insertion order."""
        limit = _validate_optional_limit(limit)
        if limit is not None and limit <= 0:
            return
        with self._lock:
            if limit is None:
                rows = self._conn.execute(
                    "SELECT payload FROM agent_events ORDER BY id ASC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload FROM agent_events ORDER BY id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
        for (payload,) in rows:
            yield AgentEvent.from_dict(json.loads(payload))

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteEventSink:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@dataclass
class SqliteUsageSink:
    """UsageSink that persists every recorded turn to a sqlite table."""

    path: str | Path
    _conn: sqlite3.Connection = field(init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self._conn = _open_wal(self.path)
        try:
            with self._lock:
                _ensure_schema_version(
                    self._conn,
                    component=_USAGE_SINK_COMPONENT,
                    version=_SQLITE_SCHEMA_VERSION,
                )
                self._conn.executescript(_USAGE_SCHEMA)
                self._conn.commit()
        except Exception:
            self._conn.close()
            raise

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        model = _validate_model(model)
        usage = _validate_usage(usage)
        cost_usd = _validate_cost_usd(cost_usd)
        snapshot_json = json.dumps(usage.to_dict(), ensure_ascii=False)
        from datetime import UTC, datetime

        with self._lock:
            self._conn.execute(
                "INSERT INTO usage_records (recorded_at, model, cost_usd, snapshot)"
                " VALUES (?, ?, ?, ?)",
                (datetime.now(UTC).isoformat(), model, cost_usd, snapshot_json),
            )
            self._conn.commit()

    def totals(self) -> dict[str, Any]:
        """Aggregate cost + token totals across every recorded turn."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0.0) FROM usage_records"
            ).fetchone()
            snapshot_rows = self._conn.execute(
                "SELECT snapshot FROM usage_records ORDER BY id ASC"
            ).fetchall()
        snapshots = [
            UsageSnapshot.from_dict(json.loads(snapshot))
            for (snapshot,) in snapshot_rows
        ]
        return {
            "turns": int(row[0] or 0),
            "total_cost_usd": float(row[1] or 0.0),
            "total_input_tokens": sum(s.input_tokens for s in snapshots),
            "total_output_tokens": sum(s.output_tokens for s in snapshots),
            "total_cache_write_tokens": sum(s.cache_write_tokens for s in snapshots),
            "total_cache_read_tokens": sum(s.cache_read_tokens for s in snapshots),
            "total_tool_calls": sum(s.tool_calls for s in snapshots),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteUsageSink:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
