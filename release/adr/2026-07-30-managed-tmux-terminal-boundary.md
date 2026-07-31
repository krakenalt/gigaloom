# ADR: Managed tmux terminal boundary

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

The current Textual terminal view is a bounded text projection and cannot own
a fullscreen provider TUI. A subprocess handoff also lacks durable detach,
browser attach, pane-aware liveness, and restart recovery.

## Decision

The provider owns the native terminal program and its bytes. GigaLoom owns the
terminal instance lifecycle, isolation, binding, authorization, attach
transport, liveness, recovery, and content-free receipts.

Each managed terminal uses one private tmux server/socket and a private
directory with mode `0700`. Persistent identity binds owner, workspace,
GigaLoom session, terminal name/key, native harness and optional native
session, command/cwd/executable digests, executable version, closed terminal
state, and lifecycle timestamps. Socket paths and raw authentication material
never leave the backend.

Local interactive use performs a direct `tmux attach`. Textual suspends and
hands off instead of rendering a fake terminal. Browser attach uses a fresh
authorized connection, bounded `capture-pane` seed, tmux control-mode binary
output/input, and validated resize control frames. A PTY bridge is a diagnostic
fallback, not a second public terminal product.

The state machine distinguishes `starting`, `running`, `attached`, `detached`,
`exited`, `failed`, `orphaned`, `closing`, and `closed`. Pane-dead state
overrides stale registry metadata. Detach never implies exit.

## Owner

W2 owns the provider-neutral terminal contracts, registry, tmux instance,
bridges, local attach, liveness, recovery, and terminal security policy. W6
owns the Web renderer and consumes only the public terminal protocol. Provider
adapters create launch specifications but cannot enter the generic kernel.

## Migration

The existing native handoff remains L0 passthrough while managed-terminal
capability is unavailable. W2 introduces managed instances, then replaces
`NativeTerminalScreen` with suspend/direct attach. Existing session records may
gain optional terminal bindings; absence remains truthful `passthrough` or
`unsupported`, never a synthetic managed session.

Linux and macOS enable managed mode only after exact tmux capability probing.
Windows 0.6 reports managed terminal as unsupported and preserves native
passthrough.

## Rollback

Managed terminal can be disabled by capability or configuration, falling back
to native passthrough. Rollback never restores the fake Textual terminal.
Persisted terminal metadata remains readable. Explicit, scoped reconciliation
and reaping close only verified GigaLoom-owned tmux instances; user tmux
servers are never targeted.

If the Web bridge fails, the terminal remains independently live unless an
explicit authorized stop occurs.

## Redaction and privacy

Raw terminal bytes are not persisted as transcript, evidence, logs, traces, or
notifications by default. Receipts contain identities, states, timestamps,
bounded reason codes, and reviewed digests only.

The Web path blocks or explicitly gates OSC 52 clipboard writes, never
auto-opens hyperlinks, treats titles and escape sequences as untrusted, and
does not expose socket paths. Origin, owner, workspace, session, and terminal
revision are revalidated at every upgrade and reconnect.

## Compatibility

JSON/JSONL, pipes, redirects, CI, help/version, and headless invocations remain
provider-native and do not initialize tmux, Textual, or Web runtime. Unknown or
unsupported terminal capability degrades visibly to native passthrough.

No shared/read-only browser attach, remote SSH execution, or Windows ConPTY
claim is made in 0.6.

## Bounded 0.6 slice

The slice implements one provider-neutral managed-terminal kernel, direct local
attach, one xterm-compatible browser transport, restart reconciliation, and
bounded orphan cleanup on Linux/macOS. It does not interpret provider screen
semantics, persist complete terminal output, or become a scheduler.

## Hermetic acceptance matrix

| Fixture | Required result |
| --- | --- |
| Normal prompt / alternate screen | Native bytes and screen mode preserved |
| ANSI, Unicode, wide glyphs, bracketed paste | Byte-safe bounded transport |
| Large or slow output | Bounded queue and backpressure |
| Immediate exit / retained dead pane | `exited` differs from `detached` |
| Detach with live process | Reattach succeeds without relaunch |
| Double launch | One instance through per-terminal ensure lock |
| Resize storm | Validated, coalesced, bounded updates |
| Slow/disconnected Web client | Terminal lifecycle remains live |
| Server restart / orphan metadata | Reconciled to exact truthful state |
| Missing or unsupported tmux | Explicit passthrough/unsupported result |
| OSC 52, hyperlink, hostile title | No implicit browser authority |
| Cross-owner/workspace/revision attach | Forbidden before socket resolution |
| Windows | Native passthrough; no fake managed terminal |
