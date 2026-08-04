# Operations

GigaLoom is local-first. Runtime state lives under `~/.gigaloom`, while
project-scoped state lives under `.giga/` in a registered project.

## Start and inspect

```sh
giga doctor
giga ui
```

The browser UI binds to `127.0.0.1:8091` by default. Do not expose it on an
untrusted network without the explicitly documented remote identity profile.

## Managed terminal prerequisites

Provider-native passthrough remains available anywhere the provider CLI itself
can run. The managed terminal kernel is a separate capability:

- Linux and macOS require `tmux` on `PATH` and a parseable `tmux -V` result;
- Windows reports managed tmux as unsupported and keeps native passthrough;
- local attach requires an interactive terminal;
- each managed terminal uses a private, owner-bound tmux instance rather than
  the user's default tmux server.

Check both the provider CLI and terminal capability before starting a managed
session:

```sh
tmux -V
giga doctor
```

If tmux is missing, invalid, or later disabled, GigaLoom must report that
capability honestly. It does not restore a GigaLoom-owned provider terminal UI
or claim structured resume. Existing content-free terminal lifecycle records
remain readable; cleanup and recovery stay scoped to the exact managed
instance.

## Backup and recovery

Stop active GigaLoom processes before copying state. Back up the complete
`~/.gigaloom` directory and any project `.giga/` directories so SQLite
files, JSON/JSONL records, evidence, and metadata remain consistent.

Package uninstall does not remove user state. Restore into the same paths only
while GigaLoom is stopped, then run `giga doctor`.

For the one-way 0.6 root cutover, use `giga state migrate --json`. Migration
evidence and the verified private backup are stored separately under
`~/.gigaloom-migration`; reports contain counts, schema versions, and digests,
not state contents. `giga state rollback` restores that backup to the legacy
root and deliberately leaves the canonical root in place for diagnosis.

Run state rollback with the 0.6 executable before reinstalling an older
package. Never make an older executable read `~/.gigaloom` as a substitute for
restoring the verified historical root. See
[Installation](installation.md#roll-back-an-upgrade) for the exact order.

For the 0.6→0.7 Native Agent Gateway upgrade, stop all state owners and run
`giga state upgrade --backup <outside-data-dir>.zip --json`. This is distinct
from the earlier root cutover: it creates a full verified archive, migrates
legacy session project bindings, retires Textual-only preferences without
converting them into Web settings, and records a content-free ordered receipt.
An interrupted invocation is resumed with the same backup path. If recovery is
required, keep GigaLoom stopped, verify the archive, then use
`giga state restore <archive> --replace --json` before reinstalling 0.6.

## Upgrade from 0.8.1 to 0.9

Stop GigaLoom owners and create a verified backup before changing the package.
The 0.9 data is additive: existing sessions are not rewritten into Thread Relay
records, old attachment records remain readable without charset evidence, old
Context Lens clients may ignore new fields, and `.giga` parsers remain the
runtime source of truth. Route overlays are never made default automatically,
and provider-native homes are not migrated.

After upgrade, run `giga doctor`, open an existing project/session, preview an
old attachment, inspect Effective Instructions, and use gateway `--dry-run`
before starting a managed sidecar. Keep the pre-upgrade archive until these
checks and the required work journey succeed.

For rollback, stop GigaLoom and its owned managed sidecar lease, disable the 0.9
gateway profiles, reinstall 0.8.1, and restore the verified archive only through
the existing state restore owner. The older executable may ignore or quarantine
unknown additive records; do not delete immutable launch/delivery receipts or
manually rewrite SQLite/JSON state. Removing `gpt2giga 0.3` disables new routes
explicitly and must not remap them to a legacy gateway.

## Troubleshooting

- Missing provider: install its native CLI and use its native login/status
  command.
- Missing managed terminal: install tmux on a POSIX host or use the
  provider-native passthrough that `giga doctor` reports.
- Refused action: review the requested scope; do not bypass a failed approval
  or policy check.
- Stale browser assets: reinstall the released package. Source contributors
  should rebuild the frontend before syncing Python dependencies.
- Optional gateway unavailable: verify the `gpt2giga` extra is installed; no
  gateway source checkout is expected.
- Gateway route blocked: inspect the exact compatibility/preflight reason;
  never force a different protocol, provider, model, or agent as fallback.

## Quality baseline

The repository owns a separate GigaLoom coverage badge. The split baseline is
**84.59%**, measured on 2026-07-29 by the non-live standalone test gate. It is a
recorded baseline, not a claim about an unverified remote run. The quality gate
requires at least 80% coverage and excludes opt-in live provider tests.
