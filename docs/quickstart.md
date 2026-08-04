# Quickstart

Install GigaLoom and check the local environment:

```sh
uv tool install gigaloom
giga doctor
giga --version
```

GigaLoom does not replace agent authentication. Sign in with the native Codex,
Claude, or Gemini CLI before launching it through GigaLoom.

## Scenario A: native Codex, Claude, or Gemini

Add `giga` before the command you already use. Everything after the agent name
is passed to its CLI unchanged:

```sh
giga codex exec --json "review this repository"
giga claude -p "review this repository"
giga gemini -p "review this repository"
```

To use the browser interface, run:

```sh
giga ui
```

Open `http://127.0.0.1:8091/`, select a project, and create a run. The interface
listens only on the local machine by default.

## Scenario B: an ACP agent through gpt2giga

Install GigaLoom with the optional `gpt2giga` gateway:

```sh
uv tool install --force \
  --with 'gpt2giga>=0.3.0,<0.4.0' \
  'gigaloom[gpt2giga]'
```

Find OpenCode in the ACP Registry, inspect the install plan, and confirm it:

```sh
giga agent search opencode --refresh --json
giga agent add opencode --dry-run --json
giga agent add opencode --yes --json
giga agent inspect opencode --json
```

The inspection must report that the agent is ready for the selected gateway.
Then launch it with a model returned by the gateway's `/models` endpoint:

```sh
giga --with gpt2giga --model GigaChat-2-Max opencode
```

GigaLoom creates configuration only for this launch. It does not modify
`~/.opencode` and does not silently select another model provider when the
route is unavailable. If the agent supports only its native provider, launch
it without `--with` and `--model`.

## If launch is blocked

Repeat route resolution without launching the agent:

```sh
giga --with gpt2giga --model GigaChat-2-Max --dry-run --json opencode
```

Do not substitute a model or gateway address by trial and error. Find
`reason_ids` in the JSON and use [Launch troubleshooting](troubleshooting.md).
See [Agent runtimes](agent-runtimes.md) for installation details and
[Gateway integration](gateway-integration.md) for routes and models.

## Next steps

- [Agent runtimes](agent-runtimes.md): search, install, inspect, update, and
  remove agents.
- [Gateway integration](gateway-integration.md): models, routes, and launch
  modes.
- [Work, threads, and context](work-threads-and-context.md): everyday work in
  the browser interface.
- [Operations](operations.md): backups and the local service.
- [Security](security.md): access, network, and secret storage.
