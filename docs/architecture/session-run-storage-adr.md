# ADR: Per-run current-state storage

Status: accepted for roadmap slice T02-5 on 2026-07-29.

## Context

Session runs are currently retained in one authoritative `runs.jsonl` file per
session. Updating one run parses and rewrites every retained run. The cost of a
single update therefore grows with session history, even though the changed
state is bounded to one run.

The replacement must preserve transparent recoverable files, append order,
redaction, per-session serialization, Windows-safe replacement, legacy state,
and rebuildable SQLite projections. It must not weaken the existing durability
barrier or make a derived index authoritative.

## Options

### Append-only versioned run-state log

Each patch could append a new version and a latest-offset projection could
resolve current state. This keeps writes sequential, but retained history grows
with every patch, compaction becomes another migration, and append-order pages
must distinguish run identity from state-version order.

### Per-run current-state files

Each run can have one atomically replaced current-state file containing its
stable append position. A small append-order file remains a rebuildable
projection. Updating one run then reads and replaces one bounded file.

## Decision

Use per-run current-state files.

- `runs.jsonl` remains immutable migration input for legacy sessions.
- Authoritative current records live under `run_records/`. Filenames are
  SHA-256 digests of run IDs, so caller-provided IDs cannot escape the session
  directory.
- Each record stores a schema version, its immutable append position, and the
  redacted run payload.
- `run_order.jsonl` and `run_revision.json` are derived. They can be deleted or
  rebuilt by scanning current-state files and sorting by position.
- Migration is serialized by the same per-session lock as append and patch. A
  durable marker precedes materialization; the revision is published only
  after every current-state file and the order projection are complete.
  Restarting with the marker repeats migration from `runs.jsonl`
  deterministically.
- A patch reads and atomically replaces only the selected current-state file.
  Temporary and destination files share a directory, all handles are closed
  before `os.replace`, and the authoritative file is flushed and synced before
  replacement.
- Concurrent patches take one per-session process/thread lock, reread current
  state inside that lock, and therefore preserve fields written by an earlier
  patch.
- The existing SQLite lookup remains derived. It can be rebuilt from current
  files and append positions.

The storage is not described as transactional. A later bounded write-batch
contract may coordinate several files with explicit recovery markers, but it
must not claim crash atomicity that this decision does not provide.

## Consequences

Run-update read/write work is independent of retained run count. Full export
and legacy `list_runs` remain explicit O(N) operations. Migration temporarily
uses additional disk space because `runs.jsonl` is retained as recoverable
input; cleanup requires a separate compatibility decision.
