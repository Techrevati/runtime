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

Several sinks — threads or processes — can share one `SqliteAuditBackend`. Each
write reads the tip and inserts inside one `BEGIN IMMEDIATE` transaction, so
writers serialize into a single chain, and a failed write rolls back instead of
holding the write lock. A third-party backend gets the same guarantee by adding
`append_chained(build)`; one that only has `append` is single-writer.

Event content never prevents a record: values JSON cannot represent are stored
via `repr`, and lone surrogates are escaped. `emitted_at` is validated before it
becomes the record's timestamp (the raw value stays in the payload).

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
- `purge_expired` removes only the contiguous oldest prefix, comparing timestamps
  as instants, so a backdated record cannot punch a hole in the chain. The prefix
  stops at the first record that is not expired, so such a record can delay later
  purges.
- `export(fmt="jsonl")` / `export(fmt="csv")` stream the chain for an auditor. CSV
  cells are quoted, and cells a spreadsheet would run as a formula are neutralized.

## Tail truncation

Deleting the *newest* rows leaves a shorter chain that is still internally valid,
so hashes alone cannot reveal it. What detects it depends on what survives:

| Situation | Detected by |
|---|---|
| The same sink that wrote the rows is still alive | `verify_chain()`, against the tip the sink remembers |
| The process restarted | only `verify_chain(expected_tip=...)` |
| No tip was published outside the log | **not detected** |

A sink resumes from whatever tip it finds at construction, so after a restart a
truncated log looks complete. To close that gap, publish `sink.tip()` to a
write-once store periodically and pass it back:

```python
tip = sink.tip()                      # ChainTip(sequence, record_hash) — publish it
sink.verify_chain(expected_tip=tip)   # invalid if the log no longer reaches that tip
```

A sink whose log was emptied by *another* process's `purge_expired` reports
truncation until it is reopened; that false alarm is the safe direction.

## Threat model — tamper-evident, **not** tamper-proof

An adversary with write access to the backing store can rewrite the *entire*
chain (recomputing every `prev_hash`) and produce an internally consistent
forgery. The HMAC envelope raises the bar — the forger also needs the key. Real
defenses are **deployer infrastructure**, not implemented here:

- keep the signing key in an HSM, never on the log host;
- give the audit DB user append-only privileges;
- periodically publish the chain tip to a write-once external log
  (e.g. S3 Object Lock).
