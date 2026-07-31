# GigaLoom release recovery

The release path is intentionally fail-closed and split into two phases.
Candidate builds never publish. They bind one `release/release.json` identity,
one exact `main` commit, the Python wheel and sdist, the public npm tarball,
content parity evidence, hashes, licenses, SBOMs, and provenance in one retained
candidate artifact. A later protected publish phase may consume only that exact
artifact; it must not rebuild release files.

The release guard accepts only the standalone `krakenalt/gigaloom` repository,
history at or after the frozen standalone anchor in `release-policy.json`, the
standard `v<release>` tag, and the exact Python/npm version mapping declared by
the release manifest. Legacy tag prefixes and metadata drift fail closed.

The committed target lock resolves the optional gateway dependency from the
public package index. Do not add a token secret, temporary index, local source
override, publisher bypass, or hand-built replacement artifact.

The primary release and recovery owner is `@krakenalt`. The named
`backup-github-maintainer` and `backup-pypi-owner` roles, plus any admitted npm
backup owner, their distinct-account and 2FA criteria, and the
unavailable-owner boundary are defined in
[`GOVERNANCE.md`](../GOVERNANCE.md). Release and public cutover remain blocked
while either recorded role is `blocked_pending_acceptance` or until the
required registry identities and protected environments are ready.

## Failure handling

- Candidate guard, build, parity, hash, SBOM, license, or attestation failure:
  keep the failed run as evidence, fix the source, and build a new candidate
  from a new commit. Never upload an unverified local artifact.
- Candidate artifact upload failure: rerun only from the same commit when no
  registry publication has started. Compare every retained candidate artifact
  digest before selecting one for protected publication.
- Tag or ancestry failure: never move the tag. Correct only an unpublished
  draft and create a new standard tag from the intended `main` commit after the
  manifest and policy are reviewed.
- Existing PyPI or npm version: stop. Published versions are immutable; advance
  both versions in the release manifest and build a new candidate instead of
  overwriting or deleting files.
- npm succeeded but PyPI failed: do not rebuild and do not republish npm.
  Preserve the exact retained candidate artifact and registry receipts, then
  retry only the missing PyPI operation if the protected recovery procedure
  proves the version absent and consumes byte-identical files.
- PyPI succeeded but npm failed: do not rebuild and do not republish PyPI.
  Preserve the exact retained candidate artifact and registry receipts, then
  retry only the missing npm operation if the protected recovery procedure
  proves the version absent and consumes the byte-identical tarball.
- Registry success followed by release-asset failure: do not rerun either
  registry publication. Repair only missing release assets from the exact
  retained candidate artifact after verifying their public hashes.
- Compromised publisher or OIDC binding: freeze both registry paths, preserve
  workflow and registry evidence, remove only the compromised binding, and
  verify published hashes. An accepted registry owner may deprecate or yank an
  unsafe version but must never delete or overwrite it.
- Pages failure: keep the previous deployment. Rebuild the same commit locally;
  do not change `url` or `baseUrl` to work around a broken documentation link.
- Primary owner unavailable: accepted backup maintainers freeze repository and
  registry release paths while preserving refs and receipts. If a required
  backup role has not accepted, stop instead of weakening protections or
  inventing a replacement identity.

Rollback means reverting release automation before any registry publication.
After either registry succeeds, recovery is forward-only with a new immutable
version or a byte-identical retry of only the missing registry operation.

## Protected publication procedure

Use `.github/workflows/release-publish.yml` only after the
`release-production` environment has required reviewers and the PyPI and npm
trusted-publisher bindings are ready. Record the candidate workflow run ID, the
full candidate commit SHA, and the candidate manifest digest before creating
the protected `v<release>` tag.

A `v*` tag push starts the initial publication path. It resolves the tag to its
commit, selects the latest successful candidate workflow run for that exact
SHA, and requires one unexpired SHA-named artifact. The protected job downloads
that exact retained bundle, records its manifest digest, and verifies its
checksums, tag, ancestry, metadata, parity, and legacy-identifier guard. The
protected job never runs a build command. The `release-production` approval
must compare the selected run, tag, and SHA with the recorded candidate evidence.

Manual dispatch is recovery-only operationally. It requires the recorded run
ID, full SHA, tag, candidate manifest digest, and an explicit recovery mode; the
same protected job and all fail-closed verification still apply.
Candidate resolution stops before publication if `release-production` is
missing or has no required reviewers, so a tag cannot silently create an
unprotected environment.

Select exactly one recovery mode:

- `initial`: both versions must be absent. Publish npm first, prove its SHA-1
  and SHA-512 integrity match the retained tarball, then publish PyPI and prove
  the wheel and sdist SHA-256 digests match.
- `recover-pypi`: npm must already contain the exact retained tarball and the
  PyPI version must be absent. Only PyPI is published.
- `recover-npm`: PyPI must already contain exactly the retained wheel and sdist
  and the npm version must be absent. Only npm is published.
- `release-assets-only`: both registries must already contain the exact
  candidate bytes. Neither registry is published; the GitHub Release is
  created last from the retained bundle.

The release guard assigns npm `next` and GitHub Pre-release without `Latest` to
alpha, beta, and release-candidate versions. Stable versions receive npm
`latest` and the GitHub `Latest` label. Recovery must keep the same derived
channel; never use a dist-tag change to bypass a registry-state failure.

If the npm package does not yet exist and a Trusted Publisher cannot be bound,
the primary npm owner may bootstrap only the exact retained tarball once from a
trusted local session with 2FA and the derived prerelease dist-tag. Never store
that token in GitHub. Continue the protected release with `recover-pypi`, then
bind this workflow and `release-production` before any later npm publication.

Any unexpected existing file, digest mismatch, malformed registry response, or
registry outage stops publication. Do not change modes to bypass that failure.
Keep the workflow run, environment approval, candidate run ID, candidate manifest
digest, registry responses, and GitHub Release URL as the release receipt. If
GitHub Release creation fails after both registries succeed, rerun only
`release-assets-only` after confirming that no release for the tag exists; never
rebuild, republish, move the tag, or overwrite a release asset.
