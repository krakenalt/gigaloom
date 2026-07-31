# ADR: Native Codex app-server, resume, and compaction contract

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

GigaLoom already has Codex structured integration. Reimplementing Codex
protocol or TUI behavior would create two incompatible owners. The 0.6 product
instead needs a compact launcher for the real Codex TUI, truthful structured
capabilities, durable attach/resume, and native compaction.

## Decision

`giga codex` launches the real Codex TUI inside the managed terminal when the
terminal capability is admitted. GigaLoom reuses and relocates the existing
app-server client, protocol, process, event, and session code; it does not
create a second JSON-RPC client.

Structured capability is admitted only by exact executable version, generated
schema, and hermetic protocol evidence for initialization, thread
start/resume, remote-TUI metadata, `thread/compact/start`, and
`contextCompaction` lifecycle. Transport is selected from that evidence:
supported local Unix socket first, explicitly verified loopback WebSocket
second, otherwise stock native TUI without structured mirror.

Each managed session receives a private `CODEX_HOME`. Reviewed configuration
is projected without mutating the user's original home. Provider auth remains
provider-owned.

Resume order is fixed:

1. reattach a live bound terminal;
2. when the terminal is dead, cold-launch and resume the exact Codex thread if
   a compatible native thread id exists;
3. without that id, fail truthfully unless the operator explicitly chooses
   `--fresh`;
4. require an explicit decision for cwd/worktree mismatch.

Compaction calls native `thread/compact/start` only for an admitted loaded
thread. Acceptance and completion come from upstream events. GigaLoom records
the observable boundary as a new `ContextManifest` revision and never runs a
second summarizer. When structured compaction is unavailable, the capability
is `native_only` and the user may use native `/compact`.

## Owner

W2 owns Codex compatibility evidence, launch specification, private home,
app-server lifecycle, event mapping, attach/resume, and compaction action. W4
owns `ContextManifest` revision semantics and consumes only the public
compaction event. The generic terminal kernel cannot import Codex modules.

## Migration

Existing Codex sessions gain optional executable evidence, terminal binding,
native thread id, and capability state. Missing evidence does not invalidate
L0 passthrough. Existing structured code is moved mechanically into the final
bounded context before behavior changes.

The first admitted version window is chosen only after the W2-B1 probe and
fixtures. This ADR deliberately does not guess a window from the currently
installed binary.

## Rollback

Protocol drift, failed schema evidence, or app-server failure disables only
structured L2 actions and retains stock native TUI passthrough. A live managed
terminal may still be reattached without structured features. Compaction never
falls back to a GigaLoom summarizer or reports synthetic success.

Rollback does not delete user-owned Codex home, managed session evidence, or
native thread identity. Scoped private managed homes follow their explicit
retention policy.

## Redaction and privacy

Auth sources, tokens, hidden reasoning, provider-owned state, raw terminal
bytes, app-server socket addresses, and complete native context never enter
browser data or ordinary diagnostics. Configuration projection records source
and digest without copying secrets into receipts.

Arena candidates receive separate homes, terminals, threads, sockets, and
worktrees and cannot observe another candidate's identifiers or artifacts.

## Compatibility

L0 native passthrough is independent of structured version admission. Unknown,
newer, older, or malformed versions retain native behavior while structured
resume/compact is `degraded`, `native_only`, or `unsupported`.

Raw arguments after `giga codex --` remain Codex-owned. Headless, piped, and
machine-output invocations never acquire UI wrappers or structured claims.

## Bounded 0.6 slice

The slice covers one lightweight Codex launcher, exact compatibility probe,
private managed home, real native TUI, live reattach, truthful cold resume, and
one native compaction action. It excludes custom summarization, provider
account routing, undocumented steering/cancel behavior, and hidden-context
inspection.

## Hermetic acceptance matrix

| Fixture | Required result |
| --- | --- |
| Version below/inside/above window | L2 only inside exact admitted evidence |
| Initialize and thread start | Exact schema and event mapping |
| Live terminal resume | Reattach without new Codex thread |
| Dead terminal with thread id | Cold native resume of exact thread |
| Dead terminal without thread id | Truthful failure or explicit `--fresh` |
| cwd/worktree mismatch | Explicit operator decision |
| Compact accepted/completed | Upstream lifecycle and new manifest revision |
| Compact failure / active non-steerable turn | Exact failure; no invented control |
| Unknown notification / app-server exit | Bounded degraded state |
| Transport reconnect | Reauthorization and protocol resynchronization |
| Unsupported structured transport | Stock TUI plus `native_only`/unsupported |
| Separate Arena candidates | No home/socket/thread/worktree crossover |
| Live Codex test | Opt-in only; never normal CI |
