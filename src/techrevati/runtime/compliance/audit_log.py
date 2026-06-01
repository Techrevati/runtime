"""
Audit log — tamper-evident, hash-chained record-keeping (EU AI Act Article 12).

Article 12 requires "automatic recording of events (logs) over the lifetime of
the system" with at least six months retention. The runtime already ships
``SqliteEventSink`` / ``SqliteUsageSink`` for durable logging, but those rows can
be edited in place after the fact. :class:`AuditLogSink` adds **tamper-evidence**
on top: every record carries the hash of its predecessor, so any after-the-fact
edit, deletion, or reordering breaks the chain and is detected by
:meth:`AuditLogSink.verify_chain`.

``AuditLogSink`` implements **both** the ``EventSink`` (``emit``) and ``UsageSink``
(``record``) protocols, so it can be attached to an ``AgentSession`` wherever an
event or usage sink is accepted — typically wrapped together with the caller's
own sink via ``FanoutEventSink`` / ``FanoutUsageSink``.

Honest scope — **tamper-evident, not tamper-proof.** An adversary with write
access to the backing store can rewrite the *entire* chain (every ``prev_hash``
included) and produce an internally consistent forgery. The optional HMAC
envelope (``signing_key``) raises the bar — a forger also needs the key, which is
never written to storage — but the real defenses (HSM-held keys, append-only DB
privileges, periodic publication of the chain tip to a write-once external log)
are deployer infrastructure, documented in ``docs/eu-ai-act/audit-log.md`` and
NOT implemented here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from techrevati.runtime.agent_events import AgentEvent
from techrevati.runtime.usage_tracking import UsageSnapshot

__all__ = [
    "AuditBackend",
    "AuditLogSink",
    "AuditRecord",
    "ChainVerification",
    "InMemoryAuditBackend",
    "RetentionPolicy",
    "SqliteAuditBackend",
]

#: ``prev_hash`` of the genesis record (sequence 0).
GENESIS_HASH = "0" * 64

_AUDIT_SCHEMA_VERSION = 1
_AUDIT_COMPONENT = "audit_log"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON for hashing — sorted keys, no insignificant space."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


@dataclass(frozen=True)
class AuditRecord:
    """A single hash-chained audit-log entry (Article 12 record-keeping).

    ``record_hash`` = ``sha256(prev_hash || canonical_json(record-without-hashes))``.
    ``signature`` is an optional detached HMAC over ``record_hash`` (present only
    when the sink was given a ``signing_key``); the key itself is never stored.
    """

    sequence: int
    timestamp: str  # ISO 8601 UTC
    event_type: str  # e.g. "agent.started", "usage.recorded"
    payload: dict[str, Any]
    prev_hash: str
    record_hash: str
    signature: str | None = None

    def _hashable(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
        }

    def compute_hash(self) -> str:
        body = self.prev_hash + _canonical_json(self._hashable())
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "record_hash": self.record_hash,
        }
        if self.signature is not None:
            d["signature"] = self.signature
        return d


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention window for audit records (Article 12 / Article 26(6)).

    ``min_retention`` is a *floor*: :meth:`AuditLogSink.purge_expired` will never
    delete a record younger than this (default > 6 months). Records are only
    eligible for purge when ``max_retention`` is set, ``purge_after_max`` is
    True, and the record is older than ``max_retention``.
    """

    min_retention: timedelta = timedelta(days=183)
    max_retention: timedelta | None = None
    purge_after_max: bool = False

    def __post_init__(self) -> None:
        if self.min_retention < timedelta(0):
            raise ValueError("min_retention must be non-negative")
        if self.max_retention is not None:
            if self.max_retention < timedelta(0):
                raise ValueError("max_retention must be non-negative")
            if self.max_retention < self.min_retention:
                raise ValueError("max_retention must be >= min_retention")


@dataclass(frozen=True)
class ChainVerification:
    """Result of :meth:`AuditLogSink.verify_chain`."""

    valid: bool
    total_records: int
    first_bad_sequence: int | None = None
    error: str | None = None


@runtime_checkable
class AuditBackend(Protocol):
    """Append-only storage for :class:`AuditRecord`. Never updates in place."""

    def append(self, record: AuditRecord) -> None: ...

    def last(self) -> AuditRecord | None: ...

    def records(self) -> Iterator[AuditRecord]: ...

    def count(self) -> int: ...

    def purge_before(self, cutoff: str) -> int:
        """Delete records with ``timestamp`` strictly older than ``cutoff``."""
        ...


@dataclass
class InMemoryAuditBackend:
    """Process-local append-only store. For tests and short-lived sessions."""

    _records: list[AuditRecord] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            self._records.append(record)

    def last(self) -> AuditRecord | None:
        with self._lock:
            return self._records[-1] if self._records else None

    def records(self) -> Iterator[AuditRecord]:
        with self._lock:
            snapshot = list(self._records)
        yield from snapshot

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def purge_before(self, cutoff: str) -> int:
        with self._lock:
            keep = [r for r in self._records if r.timestamp >= cutoff]
            removed = len(self._records) - len(keep)
            self._records = keep
        return removed


_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    sequence    INTEGER PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prev_hash   TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    signature   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp);
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
            "INSERT INTO techrevati_runtime_metadata (component, schema_version)"
            " VALUES (?, ?)",
            (component, version),
        )
        return
    observed = int(row[0])
    if observed != version:
        raise RuntimeError(
            f"unsupported sqlite schema for {component}: "
            f"version {observed}, expected {version}"
        )


def _row_to_record(row: tuple[Any, ...]) -> AuditRecord:
    return AuditRecord(
        sequence=int(row[0]),
        timestamp=str(row[1]),
        event_type=str(row[2]),
        payload=json.loads(row[3]),
        prev_hash=str(row[4]),
        record_hash=str(row[5]),
        signature=(None if row[6] is None else str(row[6])),
    )


@dataclass
class SqliteAuditBackend:
    """Durable append-only audit store backed by stdlib ``sqlite3`` (WAL mode).

    Zero-dependency default. Pass ``:memory:`` for an in-process store or an
    on-disk path for real durability.
    """

    path: str | Path
    _conn: sqlite3.Connection = field(init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with self._lock:
                _ensure_schema_version(
                    conn, component=_AUDIT_COMPONENT, version=_AUDIT_SCHEMA_VERSION
                )
                conn.executescript(_AUDIT_SCHEMA)
                conn.commit()
        except Exception:
            with suppress(Exception):
                conn.close()
            raise
        self._conn = conn

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log"
                " (sequence, timestamp, event_type, payload, prev_hash,"
                "  record_hash, signature)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.sequence,
                    record.timestamp,
                    record.event_type,
                    _canonical_json(record.payload),
                    record.prev_hash,
                    record.record_hash,
                    record.signature,
                ),
            )
            self._conn.commit()

    def last(self) -> AuditRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT sequence, timestamp, event_type, payload, prev_hash,"
                " record_hash, signature FROM audit_log"
                " ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _row_to_record(row)

    def records(self) -> Iterator[AuditRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT sequence, timestamp, event_type, payload, prev_hash,"
                " record_hash, signature FROM audit_log ORDER BY sequence ASC"
            ).fetchall()
        for row in rows:
            yield _row_to_record(row)

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        return int(row[0] or 0)

    def purge_before(self, cutoff: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM audit_log WHERE timestamp < ?", (cutoff,)
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SqliteAuditBackend:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AuditLogSink:
    """Tamper-evident, hash-chained ``EventSink`` + ``UsageSink`` (Article 12).

    >>> sink = AuditLogSink(InMemoryAuditBackend())
    >>> sink.emit(AgentEvent.started("planner", "design"))
    >>> sink.verify_chain().valid
    True
    """

    def __init__(
        self,
        backend: AuditBackend | None = None,
        *,
        retention: RetentionPolicy | None = None,
        signing_key: bytes | None = None,
    ) -> None:
        if signing_key is not None and not isinstance(signing_key, (bytes, bytearray)):
            raise TypeError("signing_key must be bytes or None")
        self._backend: AuditBackend = backend or InMemoryAuditBackend()
        self._retention = retention or RetentionPolicy()
        self._signing_key = bytes(signing_key) if signing_key is not None else None
        self._lock = threading.Lock()
        last = self._backend.last()
        self._prev_hash = last.record_hash if last is not None else GENESIS_HASH
        self._next_sequence = (last.sequence + 1) if last is not None else 0

    # -- write paths --------------------------------------------------------

    def emit(self, event: AgentEvent) -> None:
        """EventSink protocol — append a lifecycle event to the chain."""
        if not isinstance(event, AgentEvent):
            raise TypeError("event must be an AgentEvent")
        payload = event.to_dict()
        reviewer_id = None
        if event.data is not None:
            reviewer = event.data.get("reviewer_id")
            reviewer_id = str(reviewer) if reviewer is not None else None
        if reviewer_id is not None:
            payload = {**payload, "reviewer_id": reviewer_id}
        self._append(event.event.value, event.emitted_at, payload)

    def record(self, model: str, usage: UsageSnapshot, cost_usd: float) -> None:
        """UsageSink protocol — append a per-turn usage record to the chain."""
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not isinstance(usage, UsageSnapshot):
            raise TypeError("usage must be a UsageSnapshot")
        payload = {
            "model": model.strip(),
            "cost_usd": float(cost_usd),
            "usage": usage.to_dict(),
        }
        self._append("usage.recorded", _now_iso(), payload)

    def _append(self, event_type: str, timestamp: str, payload: dict[str, Any]) -> None:
        with self._lock:
            prelim = AuditRecord(
                sequence=self._next_sequence,
                timestamp=timestamp,
                event_type=event_type,
                payload=payload,
                prev_hash=self._prev_hash,
                record_hash="",
            )
            record_hash = prelim.compute_hash()
            signature = self._sign(record_hash)
            record = AuditRecord(
                sequence=prelim.sequence,
                timestamp=prelim.timestamp,
                event_type=prelim.event_type,
                payload=prelim.payload,
                prev_hash=prelim.prev_hash,
                record_hash=record_hash,
                signature=signature,
            )
            self._backend.append(record)
            self._prev_hash = record_hash
            self._next_sequence += 1

    def _sign(self, record_hash: str) -> str | None:
        if self._signing_key is None:
            return None
        return hmac.new(
            self._signing_key, record_hash.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    # -- read / verify ------------------------------------------------------

    def records(self) -> Iterator[AuditRecord]:
        return self._backend.records()

    def verify_chain(self, *, signing_key: bytes | None = None) -> ChainVerification:
        """Recompute every hash (and HMAC, if a key is given) and detect tampering.

        After a :meth:`purge_expired`, the earliest *retained* record is treated
        as a trust anchor (its ``prev_hash`` is taken as given); contiguity is
        then verified forward from there.
        """
        key = signing_key if signing_key is not None else self._signing_key
        prev_hash: str | None = None
        total = 0
        for record in self._backend.records():
            total += 1
            if prev_hash is not None and record.prev_hash != prev_hash:
                return ChainVerification(
                    valid=False,
                    total_records=total,
                    first_bad_sequence=record.sequence,
                    error="prev_hash does not match preceding record_hash",
                )
            if record.compute_hash() != record.record_hash:
                return ChainVerification(
                    valid=False,
                    total_records=total,
                    first_bad_sequence=record.sequence,
                    error="record_hash mismatch (payload altered)",
                )
            if key is not None:
                expected = hmac.new(
                    key, record.record_hash.encode("utf-8"), hashlib.sha256
                ).hexdigest()
                if not hmac.compare_digest(expected, record.signature or ""):
                    return ChainVerification(
                        valid=False,
                        total_records=total,
                        first_bad_sequence=record.sequence,
                        error="HMAC signature mismatch",
                    )
            prev_hash = record.record_hash
        return ChainVerification(valid=True, total_records=total)

    # -- export / retention -------------------------------------------------

    def export(self, *, fmt: str = "jsonl") -> Iterator[bytes]:
        """Stream the chain as ``jsonl`` (default) or ``csv`` byte chunks."""
        if fmt == "jsonl":
            for record in self._backend.records():
                yield (json.dumps(record.to_dict(), ensure_ascii=False) + "\n").encode(
                    "utf-8"
                )
        elif fmt == "csv":
            header = "sequence,timestamp,event_type,prev_hash,record_hash\n"
            yield header.encode("utf-8")
            for record in self._backend.records():
                row = (
                    f"{record.sequence},{record.timestamp},{record.event_type},"
                    f"{record.prev_hash},{record.record_hash}\n"
                )
                yield row.encode("utf-8")
        else:
            raise ValueError(f"unsupported export format: {fmt!r}")

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete records past ``max_retention`` (no-op unless configured).

        Honors ``RetentionPolicy``: nothing is purged unless ``max_retention`` is
        set and ``purge_after_max`` is True. Records younger than
        ``min_retention`` are always retained.
        """
        policy = self._retention
        if policy.max_retention is None or not policy.purge_after_max:
            return 0
        reference = now or datetime.now(UTC)
        cutoff = (reference - policy.max_retention).isoformat()
        return self._backend.purge_before(cutoff)
