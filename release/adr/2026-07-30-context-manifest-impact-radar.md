# ADR: ContextManifest and Impact Radar

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

Operators need to know which instructions, files, tools, and memory GigaLoom
selected, what it omitted, where native compaction occurred, and what code may
be affected. Provider-managed context and hidden reasoning are not observable
and must not be represented as complete knowledge.

## Decision

GigaLoom introduces a versioned, source-bound `ContextManifest` containing:

- manifest and schema identity;
- source revision and configuration digest;
- included entries and explicit inclusion reasons;
- omissions and overrides;
- compaction boundaries;
- token estimates with method and confidence;
- provider-managed unknowns;
- aggregate manifest digest.

The same source revision, configuration, and observable inputs produce the
same digest. Cache reads validate source and configuration digests. Mandatory
protected instructions cannot be excluded by a user projection, and every
omission is visible.

Context Lens is a projection of this manifest, not an alternate prompt builder.
For native Codex it reports only sources and events observable by
GigaLoom/app-server. Native compaction creates a new manifest revision linked
to the upstream boundary; it never fabricates a full post-compaction context.

Impact Radar v1 is advisory, Git-aware lexical analysis for Python imports,
symbol references, package boundaries, nearest tests, public-contract markers,
and owners when available. Dynamic imports and runtime wiring are reported as
uncertainty, not silently resolved.

## Owner

W4 owns the manifest schema, digest/cache, Context Lens compiler, Python Impact
Radar, native-context binding, APIs, and performance budgets. Instruction,
project-memory, tools, session, and provider owners supply public source
descriptors; W4 does not read their internals. W2 emits the public native
compaction event.

## Migration

New runs create schema-v1 manifests. Existing runs may expose a partial
projection with explicit `unavailable`/`unknown` entries; GigaLoom does not
reconstruct context it did not record. Old caches are invalidated rather than
upgraded without their original source/configuration digests.

Evidence and Arena records reference immutable manifest digests. They do not
copy mutable manifest content into a second owner.

## Rollback

The Lens and Impact projections may be disabled while execution continues
under existing prompt and provider contracts. The UI must then report
`unavailable`, not an empty or complete context. Immutable stored manifests
remain readable by schema version.

A stale or unknown cache/schema never falls back to an unbound projection.

## Redaction and privacy

Manifests store reviewed identifiers, relative paths, classifications, size
metadata, freshness, reasons, and digests. Secret values, full provider-owned
context, hidden reasoning, credentials, native home contents, and unapproved
file contents are excluded. Token estimates are labeled estimates and never
reveal content through an unsafe preview.

UI and evidence projections reapply owner/workspace access and redaction
instead of treating a digest as authorization.

## Compatibility

The first slice does not change prompt construction or provider request
shapes. It observes existing, explicitly exposed inputs. Unknown provider
context is a first-class value.

Schema evolution is versioned. Consumers must reject unknown mandatory fields
or degrade to an explicit unavailable projection. Impact results are advisory
and cannot themselves authorize edits, tests, or execution.

## Bounded 0.6 slice

The slice covers deterministic schema/digest/cache, inclusions/omissions/token
estimates, Context Lens, native Codex observable context and compaction
boundaries, plus lexical Python Impact Radar. It excludes semantic whole-repo
analysis, hidden provider context, language-complete dependency resolution,
and edit authority.

## Hermetic acceptance matrix

| Fixture | Required result |
| --- | --- |
| Same source/config/inputs | Same manifest digest |
| Source or config changes | New digest; stale cache rejected |
| Attempt to omit protected instruction | Rejected or retained visibly |
| Truncation/omission | Explicit entry and reason |
| Provider-managed context | Labeled unknown, never inferred |
| Native compaction event | Linked new revision and boundary |
| Missing compaction observability | `native_only`/unknown, no fake event |
| Python import/symbol change | Bounded affected files and nearest tests |
| Dynamic import/runtime wiring | Explicit uncertainty |
| Cross-owner manifest access | Denied despite known digest |
| 5k-file cold compile | At or below 3 s or measured documented baseline |
| 5k-file warm projection | At or below 300 ms |
