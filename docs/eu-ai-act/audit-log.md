# Audit log — Article 12 record-keeping

> ⚠️ **Not legal advice.** Engineering documentation of a technical control.

Article 12 requires automatic, retained logging over the system's lifetime.
`AuditLogSink` adds **tamper-evidence** on top of the runtime's durable sinks.

## How the hash chain works

Each record carries `record_hash = sha256(prev_hash || canonical_json(record))`,
where `canonical_json` is sorted-key, whitespace-free JSON. The genesis record
links to `prev_hash = "0" * 64`. Any after-the-fact edit, deletion, or reorder
changes a hash and breaks the chain:

```python
from techrevati.runtime.compliance import AuditLogSink, SqliteAuditBackend

sink = AuditLogSink(SqliteAuditBackend("audit.db"))
# ... attach to AgentSession(audit_log=sink) or via the kit ...
result = sink.verify_chain()
assert result.valid                  # False + first_bad_sequence on tampering
```

`AuditLogSink` implements both `EventSink` (`emit`) and `UsageSink` (`record`),
so a session writes lifecycle events *and* per-turn usage into one chain. On
construction it resumes from the persisted tip, so the chain survives restarts.

## Optional HMAC envelope

Pass `signing_key=...` to attach a detached HMAC over each `record_hash`. The key
is never written to storage; verification with the wrong key (or none) fails:

```python
sink = AuditLogSink(SqliteAuditBackend("audit.db"), signing_key=hsm_key)
sink.verify_chain(signing_key=hsm_key).valid
```

## Retention & export

- `RetentionPolicy(min_retention=timedelta(days=183))` — a floor; `purge_expired`
  never deletes records younger than this, and only purges past `max_retention`
  when `purge_after_max=True`. After a purge the earliest retained record is a
  trust anchor and the chain re-verifies forward from there.
- Because the default `verify_chain()` anchors at the earliest *retained* record,
  it cannot tell a legitimate purge from **front-truncation** (an attacker dropping
  the genesis record and a prefix). On a chain you do **not** purge, call
  `verify_chain(require_genesis=True)` to additionally assert the chain starts at
  the genuine genesis (`prev_hash == "0" * 64`). To detect front-truncation *while*
  still purging, publish the chain tip to a write-once external anchor (below).
- `export(fmt="jsonl")` / `export(fmt="csv")` stream the chain for an auditor.

## Threat model — tamper-evident, **not** tamper-proof

An adversary with write access to the backing store can rewrite the *entire*
chain (recomputing every `prev_hash`) and produce an internally consistent
forgery. The HMAC envelope raises the bar — the forger also needs the key. Real
defenses are **deployer infrastructure**, not implemented here:

- keep the signing key in an HSM, never on the log host;
- give the audit DB user append-only privileges;
- periodically publish the chain tip to a write-once external log
  (e.g. S3 Object Lock).
