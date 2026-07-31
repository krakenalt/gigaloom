# ADR: Clean namespace and path cutover

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

The distribution is already named `gigaloom`, but the source package, frontend
package, embedded asset paths, entry-point groups, environment variable, state
root, console aliases, and release tag prefixes still expose names inherited
from the pre-split repository. Keeping both identities would make every later
contract and artifact ambiguous.

## Decision

GigaLoom 0.6 is a breaking alpha clean cut. The canonical identities are:

| Surface | Canonical identity |
| --- | --- |
| Python source/import | `src/gigaloom`, `gigaloom.*` |
| Frontend source/package | `web`, `@gigaloom/web` |
| Embedded frontend | `src/gigaloom/ui/web/assets` |
| Console | `giga` |
| Entry-point groups | `gigaloom.*.v1` |
| User data root | `~/.gigaloom` |
| Data-root override | `GIGALOOM_DATA_DIR` |
| Project-local root | `.giga/` |
| Release tags | `v...` |

P0-03 performs a mechanical `git mv` cutover before parallel feature work.
Normal runtime code then reads and writes only canonical paths. It provides no
old import namespace, CLI alias, route alias, entry-point group, asset id,
environment variable, or build output name.

The `.giga/` project root is retained because it is already the canonical
operator-facing project identity. Its internal files may move only when the
roadmap explicitly requires a schema migration.

## Owner

The P0 integrator owns the mechanical cutover, root metadata, package
discovery, public entry points, architecture budgets, and conflict resolution.
After P0, each workstream owns only its assigned canonical subtree. W1 owns
release and frontend packaging identities, not Python runtime internals.

## Migration

P0-04 implements a separate backup-first, journaled migration from
`~/.gpt2giga/harness` to `~/.gigaloom`.

- old-only state is checksummed, backed up, copied to a staging root, verified,
  and atomically promoted;
- new-only state is used without touching the old root;
- both roots present is a hard conflict with an explicit resolution command;
- migration is idempotent by journal and content digest;
- `GPT2GIGA_HARNESS_DATA_DIR` is not read by normal 0.6 runtime code;
- project repositories and `.giga/` content are never deleted by migration.

External adapters must republish against `gigaloom.*.v1`. Historical
changelogs and migration documentation may name old identities but cannot make
them executable.

## Rollback

Before state migration, the code cutover can be reverted as one release line.
After migration starts, rollback restores the verified backup to the old root;
it never asks the 0.6 runtime to dual-read or dual-write. The canonical new
root is preserved for diagnosis until explicit cleanup.

A failed or ambiguous migration blocks startup of mutating runtime paths.
Rollback does not restore public compatibility aliases.

## Redaction and privacy

Migration reports contain paths relative to the selected roots, counts,
schema versions, and cryptographic digests. They exclude file contents,
environment values, credentials, native provider homes, raw terminal data, and
absolute repository paths unless the operator explicitly requests a local
diagnostic.

## Compatibility

This is an intentional breaking alpha change. The only compatibility surface
is documentation plus the one-way state migrator. The separately distributed
`gpt2giga` gateway remains a valid external dependency name and is not an alias
for GigaLoom.

Unknown legacy identities in runtime source or built artifacts fail the P0-05
denylist. Unknown state schema versions and root collisions fail closed.

## Bounded 0.6 slice

The slice covers repository layout, Python imports, one console command,
entry-point groups, frontend and embedded-asset identities, release tag
prefixes, and one local data-root migration. It does not redesign persisted
business schemas, delete user state, migrate provider-owned homes, or promise
stable compatibility for the alpha line.

## Hermetic acceptance matrix

| Case | Required result |
| --- | --- |
| Fresh install | `import gigaloom` and `giga --help` work without old names |
| Built wheel/sdist/npm tarball | Legacy runtime/build denylist is empty |
| Old-only state | Backup, verified atomic migration, canonical-only runtime |
| New-only state | No migration and no old-root access |
| Both roots | Explicit conflict; no mutation |
| Crash after each migration step | Journaled retry or verified backup restore |
| Repeated migration | Same result and no duplicate state |
| Old environment override only | Clear remediation; no silent old-root use |
| Project `.giga/` state | Preserved and not deleted |
| Rollback fixture | Old backup restored without dual runtime paths |
