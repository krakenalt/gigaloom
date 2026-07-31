# Release

One GigaLoom release binds Python, npm, Git, and embedded Web assets through
`release/release.json`. For the Native Agent Gateway alpha the exact identity is:

| Surface | Identity |
|---|---|
| Canonical release | `0.7.0-alpha.1` |
| Git tag | `v0.7.0-alpha.1` |
| PyPI | `gigaloom==0.7.0a1` |
| npm | `@gigaloom/web@0.7.0-alpha.1` |

Python and npm use their native prerelease syntax but represent one release.
The root Python metadata and `web/package.json` must match the manifest.

## Tag policy

New releases use only the standard `v<release>` tag. Create the protected tag
at the exact reviewed `main` commit only after the immutable candidate has
passed its build, parity, checksum, denylist, and attestation gates. The
publish workflow requires that tag to resolve to the candidate SHA; it never
creates, moves, or repairs a tag.

Historical prefix-shaped tags remain history. Do not reuse those prefixes for
new releases. Never move or delete a tag after either registry accepts the version. A
bad unpublished tag is an audited repository-policy correction, not something
the workflow guesses around.

## Maintainer checklist

1. Confirm the named GitHub and PyPI backup owners have accepted access with
   2FA under the
   [governance policy](https://github.com/krakenalt/gigaloom/blob/main/GOVERNANCE.md).
2. Update both package changelogs and confirm every identity in
   `release/release.json`, `pyproject.toml`, and `web/package.json`.
3. Build frontend assets and run the complete non-live quality gate.
4. Run the build-only candidate workflow from the current `main` tip.
5. Verify its wheel, sdist, npm tarball, metadata, shared Web content digest,
   checksums, SBOM, licenses, isolated installs, and attestation.
6. Ensure the release commit is on `main` and the documented main/tag rulesets
   are active.
7. Confirm PyPI and npm Trusted Publishers name the exact project/package,
   repository, publish workflow, and `release-production` environment. Confirm
   its required reviewers are ready.
8. Record the candidate run ID, full source SHA, and
   `candidate-manifest.json` SHA-256 out of band.
9. Create the protected standard tag at that exact source SHA.
10. Dispatch the protected publish workflow with the recorded candidate
    identity and the correct recovery mode.

## Two-phase workflow

`.github/workflows/publish-pypi.yml` is manually dispatched to build and attest
one retained candidate. It has no registry or GitHub Release publication step.

`.github/workflows/release-publish.yml` is a separate manual workflow protected
by the `release-production` environment. It downloads the retained candidate
by run ID and full SHA, verifies the operator-provided manifest digest,
rechecks the tag, ancestry, metadata, checksums, byte parity, and legacy
denylist, and does not rebuild. It then checks public registry state before
requesting OIDC credentials.

Choose exactly one mode:

- `initial`: both versions are absent; publish npm with provenance, then PyPI;
- `recover-pypi`: exact npm bytes exist and PyPI is absent; publish only PyPI;
- `recover-npm`: exact PyPI files exist and npm is absent; publish only npm;
- `release-assets-only`: both registries contain the exact candidate bytes;
  publish neither registry and create the GitHub Release last.

Any unexpected existing filename or digest, malformed registry response, or
registry outage stops the workflow. A successful registry is never
republished during recovery.

## Rollback and recovery

Before any registry accepts a version, revert release automation or source and
build a new candidate from a new commit. After npm or PyPI succeeds, the
version and tag are immutable. Reuse only the exact retained candidate to
finish the missing registry, or advance all release identities and issue a new
version. Never rebuild a partial release, overwrite package files, or move its
tag.

Public Trusted Publisher registration, tags, GitHub releases, and package
publication are external mutations and remain separate authorized gates. See
the repository's
[release recovery runbook](https://github.com/krakenalt/gigaloom/blob/main/.github/RELEASE_RECOVERY.md).
