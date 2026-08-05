# Install and inspect agents

GigaLoom works with three agent sources:

| Source | What GigaLoom does | Where to start |
|---|---|---|
| Built-in profile | Launches an already installed Codex, Claude, Gemini, or Pi CLI | `giga agent list` |
| ACP Registry | Downloads the selected artifact into GigaLoom's managed directory | `giga agent search` |
| Local manifest | Registers a description of a program that you manage | `giga agent add --manifest` |

An ACP Registry install does not change global npm, Python, or `PATH` state.
Artifacts and probe results stay under `GIGALOOM_DATA_DIR` (by default
`~/.gigaloom`). GigaLoom does not modify the agent's native home.

## Find and install an agent

Refreshing the Registry is an explicit network action. Refresh it once, then
inspect the install plan:

```sh
giga agent search opencode --refresh --json
giga agent add opencode --dry-run --json
```

The plan binds the version, source, checksum, local id, and filesystem effects.
Confirm the same selection when it is correct:

```sh
giga agent add opencode --yes --json
```

Choose a local alias if the id is already in use:

```sh
giga agent add opencode --as team-opencode --dry-run --json
giga agent add opencode --as team-opencode --yes --json
```

Use `--allow-unverified` only after checking the source and accepting the risk
of an artifact without a verified checksum.

## Inspect state and probe again

```sh
giga agent list --json
giga agent inspect opencode --json
giga agent probe opencode --json
```

`inspect` reads the stored result. `probe` starts a short check in an isolated
environment: the agent initializes, but GigaLoom does not create a session or
send a model request.

Four `provider_bridge` states matter:

| State | Meaning | Action |
|---|---|---|
| `ready` | The agent can use a verified model provider and gateway route | You can select `--with` and `--model` |
| `native_only` | The agent works but cannot safely change its model provider | Launch without a gateway |
| `unknown_until_reprobe` | An older GigaLoom version created the record | Run `giga agent probe <id> --json` |
| `blocked` | The agent advertised support, but the current check failed | Fix the reason and probe again; do not bypass the refusal |

ACP transport alone does not imply support for an arbitrary OpenAI endpoint.
An agent without a configurable model provider remains `native_only`. This is
not an installation failure; native launch continues to work.

## What installation does

GigaLoom:

1. selects a distribution for the current platform;
2. presents a time-bounded install plan;
3. downloads the exact artifact after confirmation;
4. verifies its checksum when the Registry publishes one;
5. extracts it into the managed directory;
6. runs a probe without external network access;
7. activates the new revision only after a successful probe.

macOS uses the system `sandbox-exec` for network isolation. Linux uses
Bubblewrap (`bwrap`). Windows and Linux hosts without Bubblewrap do not yet
support managed Registry installation; install the agent CLI yourself and
register a local manifest instead.

If the first inventory request returns `503 Agent inventory unavailable`, run
an explicit refresh:

```sh
giga agent search "" --refresh --json
```

The CLI and browser interface must use the same `GIGALOOM_DATA_DIR`.

## Update or remove an agent

```sh
giga agent outdated --refresh --json
giga agent update opencode --yes --json
giga agent inspect opencode --json
```

An update is installed beside the previous revision. It is not activated when
the new probe fails. To return to a retained revision, run:

```sh
giga agent rollback opencode --yes --json
```

Inspect the removal plan before confirming it:

```sh
giga agent remove opencode --dry-run --json
giga agent remove opencode --yes --json
```

Removing a managed install does not delete a native CLI, its home directory,
or model-provider credentials.

## Register a local manifest

Use a local manifest for a program that is already installed. GigaLoom stores
the file reference and digest; it does not copy the program or grant new
authority:

```sh
giga agent add --manifest ./my-agent.toml --dry-run --json
giga agent add --manifest ./my-agent.toml --json
giga agent inspect my-agent --json
giga agent probe my-agent --json
```

If the manifest changes or disappears, the profile is stale until you replace
it deliberately.

## Reproduce installs on another machine

```sh
giga agent lock --output agent-lock.json --json
giga agent sync --lock agent-lock.json --yes --json
```

The lock file records versions and integrity facts, but not credentials. See
[Launch troubleshooting](troubleshooting.md) for refusal reasons and safe
recovery actions.
