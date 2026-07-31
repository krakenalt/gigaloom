# ADR: Multi-registry release identity

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

GigaLoom ships a Python distribution and the same frontend bytes both as an
npm package and inside the Python wheel. Independent version sources or
publish jobs could create a split release that cannot be reproduced or safely
recovered.

## Decision

`release/release.json` is the single machine-readable identity for a release
candidate. For the first 0.6 alpha it binds:

- release `0.6.0-alpha.1`;
- Git tag `v0.6.0-alpha.1`;
- Python `gigaloom==0.6.0a1`;
- npm `@gigaloom/web@0.6.0-alpha.1`.

The root Python metadata and `web/package.json` must equal the manifest.
Release guards accept only the exact repository, exact standard tag, exact
checked-out full SHA, and the standalone-repository history floor. Historical
tags are immutable; new `gigaloom-v...` and `gpt2giga-harness-v...` tags are
rejected.

The workflow has two phases:

1. `release-candidate` builds, tests, packs, inventories, hashes, attests, and
   retains wheel, sdist, npm tarball, SBOM, licenses, and provenance without
   publication.
2. `publish-release` requires a protected environment and explicit owner
   approval, consumes only the retained candidate by digest, rechecks both
   registries, publishes npm and PyPI, then creates the GitHub Release.

Frontend production happens once. The npm tarball and Python embedding carry
the same per-file content manifest, aggregate digest, source SHA, and release
manifest digest. Python installation never invokes npm, and npm installation
never invokes Python.

## Owner

W1 owns `release/**`, release scripts, release workflows, frontend package
metadata, and artifact-parity verification. The integrator owns root
`pyproject.toml`, `uv.lock`, version aggregation, and final candidate SHA.
Only the repository owner can authorize external publication.

## Migration

P0-03 removes old package and tag identities. W1 adds the manifest, public npm
metadata, parity verifier, release guard, and build-only candidate workflow.
Existing published versions and tags are recorded as history and are never
renamed or moved.

Availability and ownership of the `@gigaloom` npm scope must be confirmed at
the external authorization gate. An unavailable scope blocks publication; no
fallback package name is selected automatically.

## Rollback

Before publication, discard or revert the candidate and build a new candidate
from a new SHA. After either registry accepts a version, that version and its
tag are immutable; recovery uses the exact retained artifacts and the partial
publication runbook, or issues a new version. A published tag is never moved.

The GitHub Release is created last and never serves as proof that both
registries succeeded.

## Redaction and privacy

Published artifacts contain only the declared file set, relative source
metadata, hashes, SBOM, license evidence, and public provenance. Verification
rejects credentials, local certificates, raw traffic, absolute local paths,
test fixtures, undeclared source maps, and secret-bearing environment values.

Registry authentication remains environment-owned and never enters candidate
artifacts, logs, manifests, or receipts.

## Compatibility

Python and npm versions intentionally use their native version syntax while
representing one release identity. Consumers may use either artifact
independently. The wheel must work without Node/npm; the npm package must work
without Python.

Old tag prefixes and the private frontend package are unsupported for new
releases. Existing historical artifacts remain untouched.

## Bounded 0.6 slice

The slice covers one manifest, one public npm package, one shared asset
manifest, dry-run registry checks, a build-only candidate workflow, and a
protected publish workflow. It does not create a tag, claim npm scope
ownership, publish to a registry, or create a GitHub Release.

## Hermetic acceptance matrix

| Case | Required result |
| --- | --- |
| Manifest/metadata agree | Guard accepts exact Python/npm/tag mapping |
| Any version or repository mismatch | Guard fails before build/publish |
| Tag not at checked-out SHA or history floor | Guard fails closed |
| Old tag prefix | Guard rejects new release |
| npm pack dry run | Only declared public files are present |
| Wheel and npm extraction | Frontend bytes and aggregate digest match |
| Source SHA mismatch | Candidate rejected |
| Registry version already exists | Publish phase refuses mutation |
| Fake partial npm/PyPI success | Runbook selects retained-artifact recovery |
| Missing npm scope authorization | Stop at external release gate |
| Wheel without Node; npm without Python | Both isolated install smokes pass |
