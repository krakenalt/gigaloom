# Durability, recovery, and performance contracts

This guide records the operational contracts that remain stable while the
GigaLoom package is organized into bounded contexts. It complements the
[package-layout ADR](package-layout.md) and the
[per-run storage ADR](session-run-storage-adr.md).

## Final package layout and ownership

Production behavior belongs to one named context:

```text
gigaloom/
  attachments/  automation/  cli/          contracts/   core/
  diagnostics/  execution/   harnesses/    integrations/ native/
  projects/     providers/   review/       runtime/      sessions/
  skills/       tools/       tui/          ui/
```

Domain contexts own state and policy. `execution` coordinates their public
facades. CLI, TUI, Web, and diagnostics adapt those application APIs; they do
not become alternative state owners. Root Python modules outside this tree are
temporary compatibility shims only. The versioned architecture manifest is the
source of truth for every remaining shim, owner, and removal gate.

## Authoritative and derived state

| State | Authority | Rebuild rule |
| --- | --- | --- |
| Session metadata, messages, events, attachments, and per-run current records | Redacted files below the configured Harness data directory | Never discard to repair an index |
| Durable jobs, attempts, approvals, workers, leases, outbox, and side-effect records | Runtime SQLite database | Migrate forward under the runtime schema; do not reconstruct from UI projections |
| Session catalog, lookup tables, run order, revisions, and SQLite read models | Derived indexes or projections | May be deleted and rebuilt from authoritative session records |
| Browser/TUI query caches, SSE pending queues, generated frontend assets, benchmark reports | Derived or exported artifacts | Re-fetch, regenerate, or recreate; they are not recovery sources |
| Project configuration and project-local `.giga/` state | Files in the project workspace | Back up or version separately from the Harness user-data archive |

The configured user-data directory is normally
`~/.gpt2giga/harness` and can be changed with
`GPT2GIGA_HARNESS_DATA_DIR`. Redaction happens before authoritative
persistence and before API/UI serialization. A support export is content-free;
a state backup is private user data and is not redacted.

## Migration and rebuild

- Legacy `runs.jsonl` remains immutable migration input. Per-run authoritative
  records are materialized under a durable marker; interruption is safe because
  reopening repeats the migration deterministically.
- `run_order.jsonl`, run revisions, the session catalog, and session SQLite
  projections are rebuildable. A stale or missing projection must not override
  a completed authoritative write.
- Runtime SQLite schema migrations are forward-only and run against the
  authoritative coordination database. Stop UI processes and workers and make
  a verified backup before changing package versions.
- Structural Python moves do not change persisted schemas, `fsync` barriers,
  locks, leases, cancellation, reconciliation, API bodies, SSE events, or CLI
  exit codes.
- Packaged Cockpit assets are derived from the exact source commit. Rebuild
  them before an editable sync or package validation; the sealed wheel and
  sdist consume the verified asset manifest without running Node.

## Event durability classes

Retained events are redacted before append and use a bounded group of at most
64 records.

| Class | Examples | Flush contract |
| --- | --- | --- |
| `critical_control` | Approval, warning, recovery, and unknown control events | Conservatively ends and persists the current group before control continues |
| `final_state` | `run_finished`, `run_canceled`, `error`, completed tool/command/message events | Ends and persists the current group before the terminal state is exposed |
| `presentation_delta` | Message, stdout/stderr, reasoning, and tool-call deltas | Eligible for bounded grouping; never turns a retained event into an in-memory-only authority |

`append_event` preserves the legacy synchronous guarantee: the retained record
is persisted before the call returns. Classification controls batching and
flush boundaries, not whether a retained control or terminal event is durable.
SSE overflow asks the client to resnapshot from durable state rather than
claiming that an unbounded in-memory stream is complete.

## Worker maintenance cadence

Each worker tracks independent monotonic deadlines:

| Task | Default cadence |
| --- | ---: |
| Worker and owned-attempt heartbeat | 2 seconds |
| Schedule trigger scan | 5 seconds |
| Retry requeue | 5 seconds |
| Expired-attempt recovery | 5 seconds |
| Runtime/session reconciliation | 30 seconds |

An explicit request can move one deadline earlier. Completing one task advances
only that task, so a frequent heartbeat cannot starve recovery or
reconciliation. Idle polling backs off from 0.25 to at most 1 second by default,
is bounded by the earliest database or maintenance deadline, and can be
interrupted by a best-effort content-free loopback wake signal. If loopback is
unavailable, bounded polling remains the fallback.

## Benchmark profiles

Run content-free diagnostics with:

```bash
giga benchmark performance --profile ci-smoke --samples 10
giga benchmark performance --profile local-detail --samples 10
giga benchmark performance --profile runtime-detail --samples 10
giga benchmark performance --profile tui-detail --samples 10
```

| Profile | Purpose | Gate semantics |
| --- | --- | --- |
| `ci-smoke` | Fast deterministic projections and local primitives | Only profile with blocking CI budgets |
| `local-detail` | Filesystem, SQLite, session/history, CLI, worker, Web, and TUI detail | Reference evidence; machine-sensitive budgets are non-blocking |
| `runtime-detail` | Queue claims, leases, worker cadence/wakeup, recovery, SQLite contention, revisions, and application reads | Runtime regression and scaling evidence |
| `tui-detail` | Startup, navigation, rendering, command palette, and large-session interaction | Terminal interaction and rendering evidence |

The CLI defaults to five samples; release and refactor gates use the sample
count named by their plan. `--output PATH` writes canonical private JSON with
mode `0600`. Reports are content-free, size-bounded, include retention metadata
(7 days for `ci-smoke`, 14 days for detail profiles), and are not promises that
absolute latency will match across machines.

## Legacy compatibility and import migration

New first-party code imports public context boundaries, for example:

```python
from gigaloom.runtime.api import RuntimeCoordinationStore
from gigaloom.projects.api import resolve_project
from gigaloom.integrations.api import IntegrationCatalogStore
from gigaloom.diagnostics.performance.api import run_performance_baseline
```

The reviewed root paths such as `gigaloom.doctor`,
`gigaloom.product_inventory`, and
`gigaloom.performance_baseline` remain compatibility shims during the
published migration window. They contain no business implementation. Remove a
shim only after production, tests, examples, docs, entry points, and an
installed wheel/sdist prove parity; otherwise retain it with an explicit owner
and removal gate.

The historical combined-prerelease namespace `gpt2giga.harness.*` is different:
the standalone distribution does not restore it. Out-of-tree adapters must use
`gigaloom.*`, public SDK/contracts, and the existing
`gigaloom.harnesses.v1` entry-point group. Validate dynamic imports and entry
points from an installed artifact, not only from a source checkout.

## Backup and rollback

Before an upgrade or rollback, stop Cockpit processes, durable workers, and
active runs for the selected data directory:

```bash
giga state backup --output ../gigaloom-state.zip
giga state verify ../gigaloom-state.zip --json
giga state restore ../gigaloom-state.zip --replace --json
```

Create the archive outside the state directory and keep it private. The backup
is versioned and content-addressed, snapshots SQLite consistently, and excludes
transient locks, WAL/SHM files, and temporary files. Project-local `.giga/`
directories need their own backup policy.

Each structural commit is independently revertible while its compatibility
shim remains. Reverse state migrations are not supported: package rollback
requires the verified pre-upgrade archive for that version. Derived indexes may
be rebuilt after restore; authoritative files and the runtime database may not
be deleted to force an older package to start.
