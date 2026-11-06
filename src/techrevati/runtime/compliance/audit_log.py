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

Writers: several sinks (threads or processes) may share one backend. The
reference backends read the chain tip and insert inside one atomic section
(``BEGIN IMMEDIATE`` on SQLite), so writers serialize instead of forking the
chain, and a failed write always rolls back rather than holding the write lock.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
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
    "ChainTip",
    "ChainVerification",
    "InMemoryAuditBackend",
    "RetentionPolicy",
    "SqliteAuditBackend",
]

#: ``prev_hash`` of the genesis record (sequence 0).
GENESIS_HASH = "0" * 64

_AUDIT_SCHEMA_VERSION = 1
_AUDIT_COMPONENT = "audit_log"
_MAX_COERCE_DEPTH = 64
# Spreadsheet formula triggers neutralized in CSV exports (OWASP CSV injection).
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON for hashing — sorted keys, no insignificant space.

    The output for any JSON-round-trippable payload is part of the on-disk hash
    format: changing it would invalidate every chain already written.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _coerce_jsonable(value: Any, *, depth: int = 0) -> Any:
    """Best-effort JSON-safe copy of ``value``; never loses the record."""
    if depth >= _MAX_COERCE_DEPTH:
        return f"<truncated at depth {_MAX_COERCE_DEPTH}>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {
            (key if isinstance(key, str) else repr(key)): _coerce_jsonable(
                item, depth=depth + 1
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_coerce_jsonable(item, depth=depth + 1) for item in value]
    return repr(value)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the exact form the payload will have after a storage round trip.

    Hashing this form (rather than the caller's object) keeps a record verifiable
    after it is read back: non-string keys, tuples and the like otherwise hash
    differently before and after JSON. Unserializable values are recorded via
    ``repr`` — an audit trail must never drop an event because of its content.
    """
    try:
        text = _canonical_json(payload)
    except (TypeError, ValueError, RecursionError):
        try:
            text = _canonical_json(_coerce_jsonable(payload))
        except (TypeError, ValueError, RecursionError) as exc:
            text = _canonical_json({"_unserializable": type(exc).__name__})
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _hash_bytes(text: str) -> bytes:
    """UTF-8, passing lone surrogates through so hashing cannot fail on content."""
    return text.encode("utf-8", "surrogatepass")


def _storable_json(value: Any, **kwargs: Any) -> str:
    """JSON text safe for UTF-8 storage; escapes only when lone surrogates exist."""
    text = json.dumps(value, ensure_ascii=False, **kwargs)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(value, ensure_ascii=True, **kwargs)
    return text


def _parse_timestamp(value: str) -> datetime | None:
    """Parse a timezone-aware ISO 8601 timestamp to UTC, or ``None`` if invalid."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    try:
        return parsed.astimezone(UTC)
    except OverflowError:
        return None


def _expired_prefix(records: Iterator[tuple[int, str]], cutoff: str) -> int | None:
    """Return the last sequence of the contiguous expired prefix (ascending input).

    Purging stops at the first record that is not provably older than ``cutoff``,
    so a purge can never punch a hole in the chain. Timestamps are compared as
    instants; an unparseable timestamp is treated as "not expired".
    """
    limit = _parse_timestamp(cutoff)
    if limit is None:
        raise ValueError(f"cutoff must be a timezone-aware ISO timestamp: {cutoff!r}")
    last: int | None = None
    for sequence, timestamp in records:
        parsed = _parse_timestamp(timestamp)
        if parsed is None or parsed >= limit:
            break
        last = sequence
    return last


def _csv_cell(value: object) -> str:
    text = str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + text
    return text


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
        return hashlib.sha256(_hash_bytes(body)).hexdigest()

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
class ChainTip:
    """A point in the chain, for anchoring outside the log (see ``verify_chain``)."""

    sequence: int
    record_hash: str


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
        """Delete the contiguous prefix of records older than ``cutoff``.

        Stop at the first record that is not older, so the retained records stay
        one unbroken chain.
        """
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

    def append_chained(
        self, build: Callable[[AuditRecord | None], AuditRecord]
    ) -> AuditRecord:
        """Atomically read the tip, build the next record, and store it."""
        with self._lock:
            record = build(self._records[-1] if self._records else None)
            self._records.append(record)
        return record

    def records(self) -> Iterator[AuditRecord]:
        with self._lock:
            snapshot = list(self._records)
        yield from snapshot

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def purge_before(self, cutoff: str) -> int:
        with self._lock:
            last = _expired_prefix(
                ((r.sequence, r.timestamp) for r in self._records), cutoff
            )
            if last is None:
                return 0
            keep = [r for r in self._records if r.sequence > last]
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
    # INSERT OR IGNORE, then read back: concurrent cold starts must not collide.
    conn.execute(
        "INSERT OR IGNORE INTO techrevati_runtime_metadata"
        " (component, schema_version) VALUES (?, ?)",
        (component, version),
    )
    row = conn.execute(
        "SELECT schema_version FROM techrevati_runtime_metadata WHERE component = ?",
        (component,),
    ).fetchone()
    observed = int(row[0])
    if observed != version:
        raise RuntimeError(
            f"unsupported sqlite schema for {component}: "
            f"version {observed}, expected {version}"
        )


def _row_to_record(row: tuple[Any, ...]) -> AuditRecord:
    # A row edited into invalid JSON must surface as a hash mismatch from
    # verify_chain, not as a crash while reading the evidence.
    try:
        payload = json.loads(row[3])
    except (TypeError, ValueError):
        payload = {"_corrupt_payload": str(row[3])}
    return AuditRecord(
        sequence=int(row[0]),
        timestamp=str(row[1]),
        event_type=str(row[2]),
        payload=payload,
        prev_hash=str(row[4]),
        record_hash=str(row[5]),
        signature=(None if row[6] is None else str(row[6])),
    )


@dataclass
class SqliteAuditBackend:
    """Durable append-only audit store backed by stdlib ``sqlite3`` (WAL mode).

    Zero-dependency default. Pass ``:memory:`` for an in-process store or an
    on-disk path for real durability. Safe to share between sinks, threads, and
    processes: :meth:`append_chained` serializes writers with ``BEGIN IMMEDIATE``.
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

    def _insert(self, record: AuditRecord) -> None:
        self._conn.execute(
            "INSERT INTO audit_log"
            " (sequence, timestamp, event_type, payload, prev_hash,"
            "  record_hash, signature)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.sequence,
                record.timestamp,
                record.event_type,
                _storable_json(record.payload, sort_keys=True, separators=(",", ":")),
                record.prev_hash,
                record.record_hash,
                record.signature,
            ),
        )

    def _rollback_quietly(self) -> None:
        """Release the write lock after a failure; a held lock wedges all writers."""
        if self._conn.in_transaction:
            with suppress(sqlite3.Error):
                self._conn.rollback()

    def append(self, record: AuditRecord) -> None:
        with self._lock:
            try:
                self._insert(record)
                self._conn.commit()
            except BaseException:
                self._rollback_quietly()
                raise

    def append_chained(
        self, build: Callable[[AuditRecord | None], AuditRecord]
    ) -> AuditRecord:
        """Atomically read the tip, build the next record, and store it."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                record = build(self._last_row())
                self._insert(record)
                self._conn.commit()
            except BaseException:
                self._rollback_quietly()
                raise
        return record

    def _last_row(self) -> AuditRecord | None:
        row = self._conn.execute(
            "SELECT sequence, timestamp, event_type, payload, prev_hash,"
            " record_hash, signature FROM audit_log"
            " ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def last(self) -> AuditRecord | None:
        with self._lock:
            return self._last_row()

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
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                rows = self._conn.execute(
                    "SELECT sequence, timestamp FROM audit_log ORDER BY sequence ASC"
                )
                last = _expired_prefix(
                    ((int(row[0]), str(row[1])) for row in rows), cutoff
                )
                rows.close()
                removed = 0
                if last is not None:
                    cur = self._conn.execute(
                        "DELETE FROM audit_log WHERE sequence <= ?", (last,)
                    )
                    removed = int(cur.rowcount or 0)
                self._conn.commit()
            except BaseException:
                self._rollback_quietly()
                raise
        return removed

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
        # ``is None``, not truthiness: a sized backend with no rows yet is falsy.
        self._backend: AuditBackend = (
            InMemoryAuditBackend() if backend is None else backend
        )
        self._retention = retention or RetentionPolicy()
        self._signing_key = bytes(signing_key) if signing_key is not None else None
        self._lock = threading.Lock()
        last = self._backend.last()
        self._prev_hash = last.record_hash if last is not None else GENESIS_HASH
        self._next_sequence = (last.sequence + 1) if last is not None else 0
        # Set when this sink observes the stored tip behind what it already wrote.
        self._tip_anomaly: str | None = None
        # The whole chain was legitimately purged, so an empty store is expected.
        self._tip_purged = False

    @property
    def backend(self) -> AuditBackend:
        """The storage backend this sink writes to."""
        return self._backend

    @property
    def retention(self) -> RetentionPolicy:
        """The retention policy this sink honors."""
        return self._retention

    def tip(self) -> ChainTip | None:
        """Current chain tip; publish it externally to anchor ``verify_chain``."""
        last = self._backend.last()
        if last is None:
            return None
        return ChainTip(sequence=last.sequence, record_hash=last.record_hash)

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
        self._append(event.event.value, self._record_timestamp(event), payload)

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

    @staticmethod
    def _record_timestamp(event: AgentEvent) -> str:
        """UTC timestamp for the record; the raw value stays in the payload.

        ``emitted_at`` is caller-settable, so it is validated before it becomes a
        chain and retention field. An invalid value falls back to the sink's clock.
        """
        parsed = _parse_timestamp(event.emitted_at)
        return _now_iso() if parsed is None else parsed.isoformat()

    def _append(self, event_type: str, timestamp: str, payload: dict[str, Any]) -> None:
        normalized = _normalize_payload(payload)

        def build(tip: AuditRecord | None) -> AuditRecord:
            sequence, prev_hash = self._resolve_link(tip)
            prelim = AuditRecord(
                sequence=sequence,
                timestamp=timestamp,
                event_type=event_type,
                payload=normalized,
                prev_hash=prev_hash,
                record_hash="",
            )
            record_hash = prelim.compute_hash()
            return AuditRecord(
                sequence=sequence,
                timestamp=timestamp,
                event_type=event_type,
                payload=normalized,
                prev_hash=prev_hash,
                record_hash=record_hash,
                signature=self._sign(record_hash),
            )

        with self._lock:
            chained = getattr(self._backend, "append_chained", None)
            if chained is not None:
                record: AuditRecord = chained(build)
            else:
                # Third-party backend without an atomic section: single-writer.
                record = build(self._backend.last())
                self._backend.append(record)
            self._prev_hash = record.record_hash
            self._next_sequence = record.sequence + 1
            self._tip_purged = False

    def _resolve_link(self, tip: AuditRecord | None) -> tuple[int, str]:
        """Pick (sequence, prev_hash) for the next record, under the write section.

        A stored tip ahead of this sink is another writer: adopt it. A tip behind
        what this sink already wrote means rows vanished: keep linking to the
        known tip, so the gap stays visible in the chain instead of being healed.
        """
        known = self._next_sequence - 1
        if tip is None:
            if known >= 0 and not self._tip_purged:
                self._flag_tip_anomaly(f"log is empty but sequence {known} was written")
            return self._next_sequence, self._prev_hash
        if tip.sequence > known:
            return tip.sequence + 1, tip.record_hash
        if tip.sequence == known and tip.record_hash == self._prev_hash:
            return self._next_sequence, self._prev_hash
        self._flag_tip_anomaly(
            f"stored tip at sequence {tip.sequence} does not match "
            f"sequence {known} written by this sink"
        )
        return self._next_sequence, self._prev_hash

    def _flag_tip_anomaly(self, detail: str) -> None:
        self._tip_anomaly = detail
        logger.error("audit chain tip regression: %s", detail)

    def _sign(self, record_hash: str) -> str | None:
        if self._signing_key is None:
            return None
        return hmac.new(
            self._signing_key, record_hash.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    # -- read / verify ------------------------------------------------------

    def records(self) -> Iterator[AuditRecord]:
        return self._backend.records()

    def verify_chain(
        self,
        *,
        signing_key: bytes | None = None,
        require_genesis: bool = False,
        expected_tip: ChainTip | None = None,
    ) -> ChainVerification:
        """Recompute every hash (and HMAC, if a key is given) and detect tampering.

        By default the earliest *retained* record is treated as a trust anchor
        (its ``prev_hash`` is taken as given) and contiguity is verified forward
        from there, so a :meth:`purge_expired` re-verifies cleanly. The cost of
        that is that *front-truncation* — dropping the genesis record and a prefix
        of the chain — is **not** detected by the default mode.

        Pass ``require_genesis=True`` to additionally assert that the first record
        is the genuine genesis (``prev_hash == GENESIS_HASH``), closing the
        front-truncation gap. It is mutually exclusive with retention purging: a
        purged chain no longer starts at genesis and reports invalid under this
        mode. (To detect front-truncation while still purging, publish the chain
        tip to a write-once external anchor — see the audit-log threat model.)

        *Tail truncation* is checked against the tip this sink knows from its own
        writes, so deleting the newest rows is detected for the sink's lifetime.
        A sink resumes from whatever tip it finds at construction, so after a
        restart only ``expected_tip`` — a :meth:`tip` published outside the log —
        can detect it. A tip older than the earliest retained record cannot be
        compared (it may have been purged), but the log must still reach it.
        """
        key = signing_key if signing_key is not None else self._signing_key
        anchors = [(self._next_sequence - 1, self._prev_hash)]
        if expected_tip is not None:
            anchors.append((expected_tip.sequence, expected_tip.record_hash))
        seen_anchor: set[int] = set()
        prev_hash: str | None = None
        last_sequence: int | None = None
        total = 0
        for record in self._backend.records():
            total += 1
            if prev_hash is None:
                if require_genesis and record.prev_hash != GENESIS_HASH:
                    return ChainVerification(
                        valid=False,
                        total_records=total,
                        first_bad_sequence=record.sequence,
                        error="first record is not genesis (front-truncation?)",
                    )
            elif record.prev_hash != prev_hash:
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
            if key is not None and not self._signature_matches(key, record):
                return ChainVerification(
                    valid=False,
                    total_records=total,
                    first_bad_sequence=record.sequence,
                    error="HMAC signature mismatch",
                )
            for sequence, record_hash in anchors:
                if record.sequence != sequence:
                    continue
                if record.record_hash != record_hash:
                    return ChainVerification(
                        valid=False,
                        total_records=total,
                        first_bad_sequence=record.sequence,
                        error="record differs from the known chain tip",
                    )
                seen_anchor.add(sequence)
            prev_hash = record.record_hash
            last_sequence = record.sequence
        return self._verify_tail(total, last_sequence, anchors, seen_anchor)

    def _verify_tail(
        self,
        total: int,
        last_sequence: int | None,
        anchors: list[tuple[int, str]],
        seen_anchor: set[int],
    ) -> ChainVerification:
        """Fail if the stored chain stops short of a tip known to exist."""
        if self._tip_anomaly is not None:
            return ChainVerification(
                valid=False,
                total_records=total,
                error=f"tail truncation: {self._tip_anomaly}",
            )
        own_sequence = anchors[0][0]
        for sequence, _ in anchors:
            if sequence < 0 or sequence in seen_anchor:
                continue
            if last_sequence is not None and last_sequence > sequence:
                continue  # Older than the earliest retained record: purged.
            if sequence == own_sequence and total == 0 and self._tip_purged:
                continue  # This sink purged the whole chain itself.
            return ChainVerification(
                valid=False,
                total_records=total,
                first_bad_sequence=sequence,
                error=(
                    f"tail truncation: sequence {sequence} is known to exist "
                    f"but the log ends at {last_sequence}"
                ),
            )
        return ChainVerification(valid=True, total_records=total)

    @staticmethod
    def _signature_matches(key: bytes, record: AuditRecord) -> bool:
        expected = hmac.new(
            key, record.record_hash.encode("utf-8"), hashlib.sha256
        ).digest()
        # Compare bytes: compare_digest raises on non-ASCII str, and a tampered
        # signature column must read as invalid, not crash verification.
        try:
            actual = bytes.fromhex(record.signature or "")
        except ValueError:
            return False
        return hmac.compare_digest(expected, actual)

    # -- export / retention -------------------------------------------------

    def export(self, *, fmt: str = "jsonl") -> Iterator[bytes]:
        """Stream the chain as ``jsonl`` (default) or ``csv`` byte chunks."""
        if fmt == "jsonl":
            for record in self._backend.records():
                yield (_storable_json(record.to_dict()) + "\n").encode("utf-8")
        elif fmt == "csv":
            header = "sequence,timestamp,event_type,prev_hash,record_hash\n"
            yield header.encode("utf-8")
            for record in self._backend.records():
                buffer = io.StringIO()
                csv.writer(buffer, lineterminator="\n").writerow(
                    [
                        record.sequence,
                        _csv_cell(record.timestamp),
                        _csv_cell(record.event_type),
                        _csv_cell(record.prev_hash),
                        _csv_cell(record.record_hash),
                    ]
                )
                yield buffer.getvalue().encode("utf-8", "backslashreplace")
        else:
            raise ValueError(f"unsupported export format: {fmt!r}")

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete records past ``max_retention`` (no-op unless configured).

        Honors ``RetentionPolicy``: nothing is purged unless ``max_retention`` is
        set and ``purge_after_max`` is True. Records younger than
        ``min_retention`` are always retained. Only the contiguous oldest prefix
        is removed, so a backdated record cannot punch a hole in the chain; ``now``
        must be timezone-aware and is compared as an instant.
        """
        policy = self._retention
        if policy.max_retention is None or not policy.purge_after_max:
            return 0
        if now is not None and now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        reference = datetime.now(UTC) if now is None else now.astimezone(UTC)
        cutoff = (reference - policy.max_retention).isoformat()
        with self._lock:
            removed = self._backend.purge_before(cutoff)
            if removed and self._backend.last() is None:
                self._tip_purged = True
        return removed
