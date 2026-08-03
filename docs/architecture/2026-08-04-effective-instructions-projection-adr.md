# ADR: Read-only Effective Instructions projection

Status: accepted on 2026-08-04 for the GigaLoom 0.9 foundation.

## Context

Users need to understand which project instructions may affect an agent run.
GigaLoom already owns Context Manifest and Context Lens discovery. Combining
provider files into a synthesized prompt would duplicate that owner, risk
double injection, and imply compatibility that adapters have not proved.

## Decision

### Owner and discovery boundary

Effective Instructions extends Context Manifest and Context Lens as a read-only
projection. Discovery is confined to the project root and uses bounded tracked
file enumeration, safe symlink handling, ignore rules, and size/count budgets.
It may describe root/nested `AGENTS.md`, Agent Profile prompt files/selectors,
GigaLoom project rules, and provider-native project rules only when an exact
adapter identifies them without reading private user homes.

The projection does not auto-merge `.cursor`, `.claude`, `.codex`,
`GEMINI.md`, `AGENTS.md`, or GigaLoom rules. It does not materialize or inject
instructions. Provider-native homes and global configuration are neither read
nor changed by this feature.

### Projection contract

For every discovered or expected source, the projection records source path
and kind, digest, scope, precedence, inclusion/omission reason, freshness,
estimated tokens, conflicts, uncertainties, and materialization owner.
Aggregate evidence contains revisions, digests, counts, bounds, and omission
facts. Prompt/rule content is not persisted in evidence by default.

Precedence is reported from each owning adapter; GigaLoom does not invent a
cross-provider ordering. Conflicts are facts for review, not an automatic merge
decision. An unsupported source is visible as unsupported rather than silently
omitted.

Any decision that depends on an unknown or stale adapter/discovery revision
fails closed and requests revalidation. Read-only display may show stale facts
only when clearly labelled with their last revision and omissions.

## Migration and rollback

The projection is additive and stores no replacement instruction corpus.
Migration may recompute content-free digests but never rewrites source files.
Rollback disables the projection and deletes only derived cache entries under
the existing context owner; project instructions and provider homes remain
untouched.

## Consequences

Users can inspect scope, precedence, drift, conflicts, and omissions before a
run without GigaLoom becoming another prompt owner or changing what a native
agent loads.
