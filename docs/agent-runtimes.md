# Install and register coding-agent runtimes

The **Coding Agents → Agent runtimes** page combines three related but distinct
sources:

| Source | What it is | How it appears |
|---|---|---|
| Built-in profile | A reviewed description of an already installed provider CLI such as Codex, Claude, Gemini, or Pi | `giga agent list` |
| ACP Registry | An official remote catalog entry that GigaLoom installs into a private managed root | **ACP Registry** and **Installed** tabs |
| Local manifest | An advanced local TOML description of an executable or structured route that you manage yourself | **Local manifests** tab |

Registry installation does not modify global npm, Python, or `PATH` state.
Local manifest registration does not install or execute the declared program.

## Fix `503 Agent inventory unavailable` on first start

The inventory endpoint reads only a previously validated local registry
snapshot. It does not perform hidden network access. On a fresh data directory,
there is no snapshot yet, so this request can return `503`:

```text
GET /api/agent-runtimes/inventory
```

This is the expected first-start recovery flow:

1. Open **Coding Agents → Agent runtimes**.
2. Select **Refresh registry** once. This explicitly downloads and validates the
   official ACP Registry metadata; it does not install or execute an agent.
3. After the refresh succeeds, open the **ACP Registry** tab.

The equivalent CLI command is:

```bash
giga agent search "" --refresh --json
```

The validated snapshot is cached under
`GIGALOOM_DATA_DIR/agent_profiles/acp_registry/`. The default data directory is
`~/.gigaloom`. The UI server and CLI must use the same `GIGALOOM_DATA_DIR`.

If refresh still fails, run the CLI command from the same environment as the UI
server and inspect its final error. The usual causes are:

- the registry host is unavailable through DNS, TLS, a proxy, or a firewall;
- the response is not the supported bounded Registry v1 JSON document;
- the local cache pointer or snapshot fails integrity validation;
- the UI server and CLI use different data directories.

For a cache-integrity error, stop every GigaLoom process using the data
directory, move `agent_profiles/acp_registry` to a backup location, restart the
UI, and explicitly refresh again. Do not hand-edit `current.json` or snapshot
files. A stale but valid cached snapshot remains usable when a later network
refresh fails.

## Why a confirmed install can be rejected

Registry refresh and install preview are separate from artifact installation.
Successful `200` responses for inventory, refresh, preview, and operation reads
prove that the catalog path works; they do not grant permission to download and
execute an agent.

Current builds automatically admit a system-owned network-deny launcher for the
initialize-only compatibility probe:

- macOS: `/usr/bin/sandbox-exec` with external network denied and only local
  loopback traffic admitted for ACP commands that use an internal local server;
- Linux: `/usr/bin/bwrap` or `/bin/bwrap` with a private network namespace.

The confirmed server-owned transaction downloads the exact reviewed artifact,
checks its declared digest, extracts it into a private managed root, and runs
the probe through that launcher before activation. If no supported launcher is
available, the install fails closed before creating an operation with:

```text
managed_agent_network_isolation_required
```

Windows and Linux hosts without Bubblewrap remain unsupported for managed
installation; install the provider CLI outside GigaLoom and use a reviewed local
manifest there. If macOS reports `sandbox_apply: Operation not permitted`, start
`giga ui` from a normal terminal rather than from inside another restrictive
sandbox. Do not bypass the check by changing the internal admission flag.

Always pass an absolute data directory when starting an older build:

```bash
export GIGALOOM_DATA_DIR="$HOME/.gigaloom"
giga ui
```

Current builds expand `~` before creating agent registry and operation state.
If an older build already created a literal `./~/.gigaloom`, stop GigaLoom and
back that directory up before reconciling it with `$HOME/.gigaloom`; do not run
two servers against the two locations.

### Fix `binary_download_failed` for a GitHub Release

GitHub Release archive URLs normally answer with a redirect to
`release-assets.githubusercontent.com`. Current builds bind that exact asset
origin into the reviewed install plan for canonical GitHub Release URLs, follow
at most two HTTPS redirects, and still verify the downloaded archive against a
Registry SHA-256 when one is declared. HTTP redirects, lookalike hosts, and
origins absent from the plan fail closed.

After upgrading from a build that rejected every redirect:

1. Stop and restart `giga ui` with the intended `GIGALOOM_DATA_DIR`.
2. Select **Refresh registry** so cached distributions receive the new
   plan-bound network origins.
3. Create and confirm a new install preview. A failed or expired operation is
   immutable and should not be reused.

If the new operation still reports `binary_download_failed`, verify that the
machine or proxy can reach both `github.com` and
`release-assets.githubusercontent.com`. Do not disable checksum validation or
add a wildcard redirect allowlist.

## Install an agent from the ACP Registry in the UI

1. Refresh the registry and open the **ACP Registry** tab.
2. Filter by platform, distribution, integrity, or license.
3. Select **Review install** on the desired entry.
4. Review the exact version, source, managed path, integrity policy, and side
   effects in the backend-generated plan.
5. If the proposed id collides with a built-in command or another agent, enter
   a safe local alias.
6. Confirm the reviewed plan before it expires.
7. Open **Installed**. A compatible install is activated automatically. For a
   retained inactive revision, review the explanation and select **Check &
   activate**. Complete provider-owned authentication if requested, then select
   **Use in new run**.

Enable an unverified distribution only after reviewing its source and accepting
that no registry digest protects that artifact. The browser never chooses a
distribution on its own; the backend selects one for the current platform and
binds confirmation to the reviewed plan.

## Install an agent from the ACP Registry in the CLI

The CLI discovers the same platform isolation launcher as the UI.

First refresh and inspect the available ids:

```bash
giga agent search "" --refresh --json
```

Preview one exact entry without installing it:

```bash
giga agent add <registry-id> --dry-run --json
```

Install the reviewed entry:

```bash
giga agent add <registry-id> --yes --json
```

If the preview reports an identity collision, choose an explicit alias:

```bash
giga agent add <registry-id> --as <local-agent-id> --dry-run --json
giga agent add <registry-id> --as <local-agent-id> --yes --json
```

Use `--allow-unverified` only when the preview shows that the selected
distribution has no verified integrity evidence and you have accepted that
risk.

Verify the resulting runtime:

```bash
giga agent list --json
giga agent inspect <local-agent-id> --json
giga agent probe <local-agent-id> --json
```

## Activate a retained inactive ACP revision

An install can succeed while activation is withheld. This means the reviewed
artifact remains in its private managed root, but GigaLoom will not select it
for a run until a fresh ACP compatibility check succeeds. It does not mean that
the global provider CLI was installed or modified.

In the UI, open **Installed** and select **Check & activate** on the inactive
revision. The card shows the exact sequence:

1. Start that retained managed command with a temporary private native home.
2. Block external network access while permitting local loopback required by
   some ACP implementations.
3. Perform ACP `initialize` and check the protocol and required capabilities.
4. Atomically select the revision only when it is compatible. A failed check
   leaves any currently active revision unchanged and stores content-free
   diagnostic evidence on the inactive revision.

The terminal equivalent is:

```bash
giga agent list --json
giga agent activate <local-agent-id> \
  --install-id <install-id> \
  --yes --json
```

`--install-id` is recommended because it binds the action to the exact card or
revision the operator reviewed. If omitted, GigaLoom selects the newest inactive
revision for that local agent id. A successful response has `"active": true`;
an incompatible fresh check returns `"active": false` and a non-zero CLI exit
without replacing the active pointer.

This activation path is agent-neutral. It uses the stored command and arguments
from any managed ACP Registry distribution (`binary`, `npx`, or `uvx`), so the
same UI and CLI flow applies to future ACP connectors as well as current ones.

Updates are side-by-side and explicit. A previous retained revision can be
restored with `rollback`:

```bash
giga agent outdated --refresh --json
giga agent update <local-agent-id> --yes --json
giga agent rollback <local-agent-id> --yes --json
```

## Register an advanced local manifest

Use a local manifest when the executable is already managed outside GigaLoom
or when you need to describe a custom native/structured route. The manifest is
a strict TOML file and must remain at its registered path.

Minimal native example:

```toml
schema_version = 1
agent_id = "my-agent"
display_name = "My Agent"
aliases = []
profile_version = "1.0.0"
auth_owner = "provider"
platform_support = ["darwin", "linux", "win32"]
compatibility_profiles = []
structured_routes = []

[source]
kind = "local_manifest"
origin = "local:my-agent.toml"
revision = "1.0.0"
trust_class = "local"
reviewed = false

[native]
executable_names = ["my-agent"]
provider_home_markers = []
provider_config_markers = []
version_probe = ["--version"]
supports_managed_terminal = true

[[native.interactive_matchers]]
matcher_id = "my-agent.root"
kind = "empty_suffix"
tokens = []
precedence = 10

[[native.interactive_matchers]]
matcher_id = "my-agent.prompt"
kind = "single_positional"
tokens = []
precedence = 20

[[native.metadata_matchers]]
matcher_id = "my-agent.metadata"
kind = "any_option"
tokens = ["--help", "--version"]
precedence = 10

[[native.headless_matchers]]
matcher_id = "my-agent.headless"
kind = "first_token"
tokens = ["exec"]
precedence = 10
```

Validate the registration without changing state, then register it:

```bash
giga agent add --manifest ./my-agent.toml --dry-run --json
giga agent add --manifest ./my-agent.toml --json
giga agent inspect my-agent --json
giga agent probe my-agent --json
```

Registration stores a digest-bound reference to the manifest. It does not copy
the executable, install dependencies, grant credentials, or make a local source
reviewed. If the file later changes or disappears, the profile is reported as
stale until it is deliberately replaced.

## Reproduce managed installs on another machine

Export exact installed revisions, then sync them on the target machine without
silently upgrading to newer registry entries:

```bash
giga agent lock --output agent-lock.json --json
giga agent sync --lock agent-lock.json --yes --json
```

Keep the lock file in an appropriate project-controlled location. It contains
artifact identity and integrity evidence, not provider credentials.
