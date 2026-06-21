"""Tests for the tamper-evident hash-chained audit log (EU AI Act Article 12)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from techrevati.runtime import AgentEvent
from techrevati.runtime.compliance import (
    AuditLogSink,
    InMemoryAuditBackend,
    RetentionPolicy,
    SqliteAuditBackend,
)
from techrevati.runtime.compliance.audit_log import GENESIS_HASH
from techrevati.runtime.usage_tracking import UsageSnapshot


def _event(role: str = "planner") -> AgentEvent:
    return AgentEvent.started(role, "design")


def test_empty_chain_is_valid() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    result = sink.verify_chain()
    assert result.valid
    assert result.total_records == 0


def test_genesis_record_links_to_zero_hash() -> None:
    backend = InMemoryAuditBackend()
    sink = AuditLogSink(backend)
    sink.emit(_event())
    first = next(backend.records())
    assert first.sequence == 0
    assert first.prev_hash == GENESIS_HASH


def test_emit_and_record_chain_is_valid() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    sink.emit(_event("planner"))
    sink.record("gpt-4o", UsageSnapshot(input_tokens=10, output_tokens=5), 0.01)
    sink.emit(_event("builder"))
    result = sink.verify_chain()
    assert result.valid
    assert result.total_records == 3


def test_sequences_are_monotonic_and_linked() -> None:
    backend = InMemoryAuditBackend()
    sink = AuditLogSink(backend)
    for _ in range(4):
        sink.emit(_event())
    records = list(backend.records())
    assert [r.sequence for r in records] == [0, 1, 2, 3]
    for prev, cur in zip(records, records[1:]):  # noqa: B905
        assert cur.prev_hash == prev.record_hash


def test_usage_record_path_uses_protocol_signature() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    # UsageSink.record(model, usage, cost_usd)
    sink.record("claude", UsageSnapshot(input_tokens=1, output_tokens=2), 0.5)
    (rec,) = list(sink.records())
    assert rec.event_type == "usage.recorded"
    assert rec.payload["model"] == "claude"
    assert rec.payload["cost_usd"] == 0.5


def test_tampered_payload_breaks_chain() -> None:
    backend = InMemoryAuditBackend()
    sink = AuditLogSink(backend)
    sink.emit(_event())
    sink.emit(_event())
    sink.emit(_event())
    # Forge the middle record's payload, keep its stored hash.
    forged = backend._records[1]
    backend._records[1] = type(forged)(
        sequence=forged.sequence,
        timestamp=forged.timestamp,
        event_type=forged.event_type,
        payload={**forged.payload, "role": "attacker"},
        prev_hash=forged.prev_hash,
        record_hash=forged.record_hash,
        signature=forged.signature,
    )
    result = sink.verify_chain()
    assert not result.valid
    assert result.first_bad_sequence == 1
    assert result.error is not None


def test_tampered_prev_hash_breaks_chain() -> None:
    backend = InMemoryAuditBackend()
    sink = AuditLogSink(backend)
    sink.emit(_event())
    sink.emit(_event())
    forged = backend._records[1]
    backend._records[1] = type(forged)(
        sequence=forged.sequence,
        timestamp=forged.timestamp,
        event_type=forged.event_type,
        payload=forged.payload,
        prev_hash="f" * 64,
        record_hash=forged.record_hash,
        signature=forged.signature,
    )
    result = sink.verify_chain()
    assert not result.valid
    assert result.first_bad_sequence == 1


def test_deleting_middle_record_breaks_chain() -> None:
    backend = InMemoryAuditBackend()
    sink = AuditLogSink(backend)
    for _ in range(3):
        sink.emit(_event())
    del backend._records[1]  # drop sequence 1
    result = sink.verify_chain()
    assert not result.valid
    assert result.first_bad_sequence == 2


def test_hmac_signature_present_and_verifies() -> None:
    key = b"super-secret-hsm-key"
    sink = AuditLogSink(InMemoryAuditBackend(), signing_key=key)
    sink.emit(_event())
    (rec,) = list(sink.records())
    assert rec.signature is not None
    assert sink.verify_chain().valid


def test_hmac_wrong_key_fails_verification() -> None:
    sink = AuditLogSink(InMemoryAuditBackend(), signing_key=b"right-key")
    sink.emit(_event())
    result = sink.verify_chain(signing_key=b"wrong-key")
    assert not result.valid
    assert result.error is not None and "HMAC" in result.error


def test_hmac_detects_forgery_even_with_consistent_chain() -> None:
    # An attacker who rewrites the whole chain (consistent hashes) still cannot
    # produce a valid HMAC without the key.
    backend = InMemoryAuditBackend()
    key = b"key"
    sink = AuditLogSink(backend, signing_key=key)
    sink.emit(_event())
    forged = backend._records[0]
    # Rewrite payload AND recompute its sha256 record_hash (no key needed for that),
    # but leave the original signature.
    rebuilt = type(forged)(
        sequence=forged.sequence,
        timestamp=forged.timestamp,
        event_type=forged.event_type,
        payload={**forged.payload, "role": "attacker"},
        prev_hash=forged.prev_hash,
        record_hash="",
        signature=forged.signature,
    )
    rebuilt = type(forged)(
        sequence=rebuilt.sequence,
        timestamp=rebuilt.timestamp,
        event_type=rebuilt.event_type,
        payload=rebuilt.payload,
        prev_hash=rebuilt.prev_hash,
        record_hash=rebuilt.compute_hash(),
        signature=forged.signature,
    )
    backend._records[0] = rebuilt
    result = sink.verify_chain()
    assert not result.valid
    assert result.error is not None and "HMAC" in result.error


def test_sqlite_persists_and_chain_continues_across_restart(tmp_path) -> None:
    db = tmp_path / "audit.db"
    backend = SqliteAuditBackend(db)
    sink = AuditLogSink(backend)
    sink.emit(_event("a"))
    sink.emit(_event("b"))
    backend.close()

    # Reopen — the sink must resume from the persisted tip.
    backend2 = SqliteAuditBackend(db)
    sink2 = AuditLogSink(backend2)
    sink2.emit(_event("c"))
    records = list(backend2.records())
    assert [r.sequence for r in records] == [0, 1, 2]
    assert sink2.verify_chain().valid
    backend2.close()


def test_sqlite_in_place_edit_detected(tmp_path) -> None:
    db = tmp_path / "audit.db"
    backend = SqliteAuditBackend(db)
    sink = AuditLogSink(backend)
    sink.emit(_event())
    sink.emit(_event())
    backend.close()

    # Edit a payload directly in the DB, like a malicious operator.
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE audit_log SET payload = ? WHERE sequence = 0",
        (json.dumps({"event": "tampered"}),),
    )
    conn.commit()
    conn.close()

    backend2 = SqliteAuditBackend(db)
    sink2 = AuditLogSink(backend2)
    result = sink2.verify_chain()
    assert not result.valid
    assert result.first_bad_sequence == 0
    backend2.close()


def test_export_jsonl_roundtrips() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    sink.emit(_event())
    sink.emit(_event())
    lines = b"".join(sink.export(fmt="jsonl")).decode("utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["sequence"] == 0
    assert "record_hash" in first


def test_export_csv_has_header() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    sink.emit(_event())
    out = b"".join(sink.export(fmt="csv")).decode("utf-8").splitlines()
    assert out[0] == "sequence,timestamp,event_type,prev_hash,record_hash"
    assert len(out) == 2


def test_export_unknown_format_raises() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    with pytest.raises(ValueError):
        list(sink.export(fmt="pdf"))


def test_purge_noop_without_max_retention() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    sink.emit(_event())
    assert sink.purge_expired() == 0


def test_purge_removes_old_keeps_recent_and_stays_verifiable() -> None:
    backend = InMemoryAuditBackend()
    sink = AuditLogSink(
        backend,
        retention=RetentionPolicy(
            min_retention=timedelta(days=1),
            max_retention=timedelta(days=30),
            purge_after_max=True,
        ),
    )
    # Two records, then hand-age the first beyond max_retention.
    sink.emit(_event("old"))
    sink.emit(_event("recent"))
    old = backend._records[0]
    aged = type(old)(
        sequence=old.sequence,
        timestamp=(datetime.now(UTC) - timedelta(days=60)).isoformat(),
        event_type=old.event_type,
        payload=old.payload,
        prev_hash=old.prev_hash,
        record_hash=old.record_hash,
        signature=old.signature,
    )
    backend._records[0] = aged
    removed = sink.purge_expired()
    assert removed == 1
    remaining = list(backend.records())
    assert len(remaining) == 1
    # The earliest retained record is a trust anchor — chain still verifies.
    assert sink.verify_chain().valid


def test_require_genesis_accepts_full_chain() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    sink.emit(_event("a"))
    sink.emit(_event("b"))
    assert sink.verify_chain(require_genesis=True).valid


def test_require_genesis_empty_chain_is_valid() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    assert sink.verify_chain(require_genesis=True).valid


def test_require_genesis_detects_front_truncation() -> None:
    backend = InMemoryAuditBackend()
    sink = AuditLogSink(backend)
    sink.emit(_event("genesis"))
    sink.emit(_event("second"))
    sink.emit(_event("third"))
    # Drop the genesis record. The remaining records are still internally
    # contiguous, so the default (anchored) mode accepts the truncated chain —
    # the documented limitation — but require_genesis catches it.
    del backend._records[0]
    assert sink.verify_chain().valid
    result = sink.verify_chain(require_genesis=True)
    assert not result.valid
    assert "front-truncation" in (result.error or "")


def test_record_rejects_bad_inputs() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    with pytest.raises(ValueError):
        sink.record("", UsageSnapshot(input_tokens=1, output_tokens=1), 0.0)
    with pytest.raises(TypeError):
        sink.record("m", object(), 0.0)  # type: ignore[arg-type]


def test_emit_rejects_non_event() -> None:
    sink = AuditLogSink(InMemoryAuditBackend())
    with pytest.raises(TypeError):
        sink.emit(object())  # type: ignore[arg-type]


def test_signing_key_type_validation() -> None:
    with pytest.raises(TypeError):
        AuditLogSink(InMemoryAuditBackend(), signing_key="not-bytes")  # type: ignore[arg-type]
