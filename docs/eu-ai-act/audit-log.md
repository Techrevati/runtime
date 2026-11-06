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

A write waits up to `SqliteAuditBackend(path, busy_timeout_seconds=30)` for other
writers, then raises. Emitting is synchronous, so under extreme contention an event
loop can block for that long; lower it if latency matters more than keeping the
record. As measured, 32 processes writing to one file peaked at about 15 s.

Event content never prevents a record, and is copied in bounded time. Only the
offending value is replaced: a cycle becomes `"<cycle>"`, an unrepresentable object
its `repr`, nesting past 100 levels or graphs past 100,000 values are truncated
with a marker, and lone surrogates are escaped. `emitted_at` is validated before it
becomes the record's timestamp (the raw value stays in the payload).

A tampered row never stops the log. Bytes that are not valid UTF-8 and payloads too
deep to parse read as a hash mismatch with `first_bad_sequence`, export still
works, and new records keep chaining past an edited tip. An unusable purge marker
reports invalid and is otherwise ignored. A row edited to the largest possible
sequence leaves no number above it, so new records take the highest free sequence
below it. Such records sort before the edited row, and the chain reports invalid.

`repr` of an arbitrary object is that object's own code. Its exceptions are
contained, but a slow `__repr__` still runs for as long as it takes.

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

## Refusing actions that cannot be recorded

By default a failed audit write is reported and the session carries on, so a logging
outage cannot take an agent down. `AgentSession(audit_fail_closed=True)` makes the
opposite trade: once a write to the session's audit log fails, every later tool call
and model turn raises `AuditWriteError`.

A tool's record is written before the tool runs, so a failing write stops that call.
A model turn is recorded when it finishes, so a write failing at that point raises
after the call was made and paid for. Starting a session writes a record too, so a log
that is already broken is caught before the first action. The session stays refused
until it ends. Writes wait up to `busy_timeout_seconds` for a lock before they count as
failed.

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

A purge records the newest record it removed, so a sink can tell that from
truncation, including a purge by another process, and the next record continues the
chain past it. What that marker proves depends on the key:

- **With a `signing_key`**, the sink that purges signs the marker with its own HMAC
  over the sequence and hash. A marker with a missing or invalid signature makes
  `verify_chain` invalid. Purge through `AuditLogSink.purge_expired`: calling
  `backend.purge_before` directly, or purging from a sink without the key, leaves an
  unsigned marker, which keyed verifiers report. A real signed marker can be left in
  place while the rows *after* it are deleted, so tail truncation after a restart
  still needs `expected_tip`.
- **Without a key, a marker proves nothing.** Anyone who can write to the database
  can delete every record and insert a marker naming the real tip's sequence and
  hash. `verify_chain` then reports valid, including with `require_genesis=True` and
  with `expected_tip`, whose anchor is weakened to a hash the attacker can read from
  the log before deleting it. Use a key if the log must withstand someone with
  database write access.

## Threat model — tamper-evident, **not** tamper-proof

An adversary with write access to the backing store can rewrite the *entire*
chain (recomputing every `prev_hash`) and produce an internally consistent
forgery. The HMAC envelope raises the bar — the forger also needs the key. Real
defenses are **deployer infrastructure**, not implemented here:

- keep the signing key in an HSM, never on the log host;
- give the audit DB user append-only privileges;
- periodically publish the chain tip to a write-once external log
  (e.g. S3 Object Lock).
