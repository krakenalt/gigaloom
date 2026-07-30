# Installation

GigaLoom supports Python 3.11–3.14. Install at least one provider-native CLI
separately and complete that provider's own authentication flow.

## Install the preview

With `uv`:

```sh
uv tool install --prerelease allow 'gigaloom==0.5.1a2'
```

Or in an isolated Python environment:

```sh
python -m pip install --pre 'gigaloom==0.5.1a2'
```

Confirm the installed artifact:

```sh
giga --version
giga doctor
```

`doctor` reports capability and configuration status without reading prompt
content or contacting providers.

## Migrate from `gpt2giga-harness`

The PyPI project name changed before the first standalone target release.
Remove the historical distribution and install `gigaloom`; do not delete the
existing state directories:

```sh
uv tool uninstall gpt2giga-harness
uv tool install --prerelease allow 'gigaloom==0.5.1a2'
```

The standalone distribution exposes the `gigaloom` Python namespace and the
single public command `giga`. It does not publish a legacy namespace or command
shim.

Before the first 0.6 startup, stop existing Harness processes, unset the removed
`GPT2GIGA_HARNESS_DATA_DIR` override, and run:

```sh
giga state migrate --json
```

The same preflight runs automatically before normal commands that use the
default root. Old-only state is backed up under `~/.gigaloom-migration`, staged,
verified, and atomically promoted to `~/.gigaloom`. The legacy root and every
project `.giga/` remain untouched. If both roots already exist without a
completed migration journal, GigaLoom stops and prints explicit `mv` commands;
it never guesses which root is authoritative. Use `giga state rollback` to
restore the verified backup to the legacy root while preserving
`~/.gigaloom` for diagnosis.

Set `GIGALOOM_DATA_DIR` to use a custom canonical root. A custom root does not
trigger migration of the two default roots.

## Optional gateway preset

The base package does not require gpt2giga. Install the optional extra only for
Direct Chat or the legacy local-gateway preset:

```sh
uv tool install --prerelease allow 'gigaloom[gpt2giga]==0.5.1a2'
```

This installs a pinned public gateway distribution. It does not require a
gateway repository, sibling checkout, editable dependency, or submodule. See
[Gateway integration](gateway-integration.md).

## Upgrade or remove

```sh
uv tool upgrade --prerelease allow gigaloom
uv tool uninstall gigaloom
```

Package removal does not delete user state under `~/.gigaloom`.
Back up or remove that state separately after reading
[Operations](operations.md).
