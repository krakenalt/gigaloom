# Release

One GigaLoom release binds Python, npm, Git, and embedded Web assets through
`release/release.json`. For the first stable Native Agent Gateway release the exact identity is:

| Surface | Identity |
|---|---|
| Canonical release | `0.7.0` |
| Git tag | `v0.7.0` |
| PyPI | `gigaloom==0.7.0` |
| npm | `@gigaloom/web@0.7.0` |

The root Python metadata and `web/package.json` must match the manifest exactly.

## Tag policy

New releases use only the standard `v<release>` tag. Create the protected tag
at the exact reviewed `main` commit. The tag starts the immutable candidate
build; publication begins only after its build, parity, checksum, denylist, and
attestation gates pass. Neither workflow creates, moves, or repairs a tag.

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
4. Ensure the release commit is on `main` and the documented main/tag rulesets
   are active.
5. Confirm PyPI and npm Trusted Publishers name the exact project/package,
   repository, publish workflow, and `release-production` environment. Confirm
   that the environment has no required-reviewer gate for automatic releases.
6. Create the protected standard tag at that exact source SHA. The tag push
   builds and attests one candidate, then automatically starts publication from
   that same workflow run after success.
7. Monitor the candidate, registry checks, and GitHub Release creation. Record
   the candidate run ID, full source SHA, and `candidate-manifest.json` SHA-256
   for recovery.

## Two-phase workflow

`.github/workflows/publish-pypi.yml` runs on a protected `v*` tag push to build
and attest one retained candidate. It has no registry or GitHub Release
publication step.

`.github/workflows/release-publish.yml` is a separate workflow protected by the
`release-production` environment. A successful completion of the candidate
workflow starts it through `workflow_run`; the resolver consumes that exact run
ID and requires one unexpired artifact with the exact SHA-bound name. The
protected job rechecks the tag, ancestry, metadata, checksums, byte parity, and
legacy denylist and does not rebuild. It then checks public registry state
before requesting OIDC credentials. Manual dispatch remains recovery-only; it
additionally requires the recorded run ID, SHA, tag, manifest digest, and
recovery mode.

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

The existing `@gigaloom/web` package must bind `release-publish.yml` plus the
`release-production` environment as its npm Trusted Publisher before the tag is
created. Do not put an npm token in GitHub.

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
