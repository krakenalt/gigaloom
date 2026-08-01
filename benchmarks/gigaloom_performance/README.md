# GigaLoom performance baseline

The dated operational-foundation snapshot under `baselines/` records the
pre-change cold CLI, API, Settings, source-inventory, and release-bump facts
used by the 0.8 architecture decision. Its absolute timings are host evidence,
not portable budgets; later gates must compare stable workload counters or
reviewed environment-matched measurements.

Run the bounded, hermetic CI smoke profile:

```bash
uv run giga benchmark performance --profile ci-smoke --samples 5 --output /tmp/gigaloom-performance.json
```

The blocking profile contains only the environment-stable in-memory session
and transcript projection budgets. Filesystem, SQLite, worker, Web, RSS,
and process-startup measurements remain in `--profile local-detail`; they are
scheduled or explicitly opt-in evidence and cannot make provider or external
network latency look like a local-code regression. All profiles use only
temporary content-free fixtures: they do not read native provider homes, send
provider traffic, or retain prompts, responses, tokens, or credentials.

Every report records the tracked performance baseline baseline and a SHA-256 fingerprint of
its Python/platform/SQLite environment. The writer rejects reports above the
profile limit: 64 KiB for CI smoke, 512 KiB for local detail, and 2 MiB for
runtime detail. Pull-request CI retains the smoke artifact for 7 days.
Nightly and manual-dispatch runs capture both detailed profiles and retain
their bounded artifacts for 14 days.

Measure the durable worker and request path locally:

```bash
uv run giga benchmark performance --profile runtime-detail --samples 20 \
  --output docs/internal/evidence/GIGALOOM_RUNTIME_PROFILE_2026-07-27.json
```

The runtime profile uses temporary content-free sessions and the local `echo`
harness. It records wall/CPU/process-peak-RSS, context-switch wakeups, bounded
SQLite statement counts, queue throughput/fairness, lock contention, worker
lifecycle/recovery, explicit loopback wake delivery, and API/SSE/Web
attribution. Schema v3 also embeds the runtime scaling baseline: 10,000-job
queue scans (including 90% incompatible jobs and a compatible job at the end
of the candidate window), 2/8-worker claims, runs-center revision queries at
100/1,000/10,000/50,000 rows, and heartbeat/idle/schedule/recovery/reconcile
cadence. Fixture creation remains outside the measured window.

The scaling section records its source commit and environment fingerprint.
`EXPLAIN QUERY PLAN` evidence contains only a hash of each SQL template,
parameter count, and SQLite plan steps; SQL parameter values, fixture IDs, and
private paths are not retained. Absolute timings remain non-blocking reference
evidence, while algorithmic counters expose connections, statements, rows
parsed, claims, duplicates, wakeups, and maintenance cycles.

The legacy runtime profile accepts a maximum 65 projected steady empty cycles
per minute and 250 ms p95 explicit wake latency. It keeps higher concurrency,
stop-on-idle ownership, and broader API/database/event repairs unselected after
the bounded runtime performance repair duplicate filesystem-scan repair; it does not access provider,
external-network, or native-home state.

The JSON report is schema-versioned. Detailed profiles record wall and CPU
percentiles, process RSS, observable block I/O, stage timings, and the full
workload contract. Metrics that are not portable or not yet observable are
`null` instead of inferred. Optimization targets remain unset until the owning
performance slice reviews a measured baseline.

## Workload registry

Workload declarations live in
`gigaloom.performance_workloads` and are discovered recursively in
deterministic family/ID order. Add a cohesive leaf module under the owning
domain package and expose a tuple named `WORKLOADS`; no central Python
inventory needs editing.

Each declaration is content-free metadata: stable ID and family, applicable
profiles, fixture variants, required metrics and algorithmic counters, and the
gate that consumes the evidence. Discovery rejects duplicate IDs, incomplete
required-family coverage, unsupported profiles, and non-typed declarations
before a benchmark starts.

The `local-detail` report also contains the non-blocking
`session_storage_baseline`. Its canonical fixtures cover 10/100/1,000-session
catalog operations, cold and warm first pages, a 5,000-message tail, run
updates at 10/100/1,000 rows, 50,000-event reads, a 100-event steady-capacity
sample, and a 500-event burst. Fixture creation is outside every measured
window. Measured writes retain the production store's real atomic replace and
`fsync` behavior.

Wall time is reference evidence because it depends on the host filesystem.
Algorithmic counters remain explicit and stable: bytes read/written, files
opened, manifest/index reads, rows parsed, atomic replaces, `fsync` calls, and
SQLite connections/statements.
