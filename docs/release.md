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
   `candidate-manifest.json` SHA-256 out of band for recovery.
9. Create the protected standard tag at that exact source SHA. The tag push
   starts the protected publish workflow and selects the latest successful,
   unexpired candidate artifact for that SHA.
10. Review the selected run and SHA at the `release-production` approval gate,
    then monitor the registry checks and GitHub Release creation.

## Two-phase workflow

`.github/workflows/publish-pypi.yml` is manually dispatched to build and attest
one retained candidate. It has no registry or GitHub Release publication step.

`.github/workflows/release-publish.yml` is a separate workflow protected by the
`release-production` environment. A `v*` tag push resolves the tag to a commit,
finds the latest successful candidate workflow run for that exact SHA, and
requires one unexpired artifact with the exact SHA-bound name. The protected
path refuses to start unless `release-production` exists with at least one
required reviewer. The protected job rechecks the tag, ancestry, metadata,
checksums, byte parity, and legacy denylist and does not rebuild. It then checks
public registry state before requesting OIDC credentials. Manual dispatch
remains available for recovery; it additionally requires the recorded run ID,
SHA, tag, manifest digest, and recovery mode.

Choose exactly one mode:

- `initial`: both versions are absent; publish npm with provenance, then PyPI;
- `recover-pypi`: exact npm bytes exist and PyPI is absent; publish only PyPI;
- `recover-npm`: exact PyPI files exist and npm is absent; publish only npm;
- `release-assets-only`: both registries contain the exact candidate bytes;
  publish neither registry and create the GitHub Release last.

Any unexpected existing filename or digest, malformed registry response, or
registry outage stops the workflow. A successful registry is never
republished during recovery.

## Release channels

The release guard derives channels from the canonical manifest version. Alpha,
beta, and release-candidate versions publish npm under the `next` dist-tag and
create a GitHub Pre-release with `Latest` explicitly disabled. A stable version
publishes npm under `latest` and explicitly marks its GitHub Release as
`Latest`. PyPI keeps the manifest's native PEP 440 prerelease or stable version.

If `@gigaloom/web` does not yet exist and npm therefore cannot bind a Trusted
Publisher, the primary npm owner must bootstrap only the exact retained `.tgz`
once from a trusted local session, with 2FA and the derived non-stable tag (for
the current alpha, `--tag next`). Do not put that token in GitHub. The protected
workflow must then resume with `recover-pypi`, and the owner must bind
`release-publish.yml` plus the `release-production` environment before any
later npm release.

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
