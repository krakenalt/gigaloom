# Quickstart

Install GigaLoom first, then verify the local environment:

```sh
giga doctor
giga --version
```

Provider authentication stays provider-owned. Sign in with the native Codex,
Claude, or Gemini CLI before asking GigaLoom to launch it.

## Prefix a native command

GigaLoom adds one prefix and preserves the remaining native command:

```sh
giga codex exec --json "summarize this repository"
giga claude -p "summarize this repository"
giga gemini -p "summarize this repository"
```

Help, version output, stdin/stdout, JSON/JSONL, and exit status remain native.
If a CLI is missing or its contract has drifted, dispatch fails closed before
starting a provider session.

## Open the browser cockpit

```sh
giga ui
```

Open `http://127.0.0.1:8091/`. The default listener is loopback-only. In the
cockpit:

1. select or register a local project;
2. choose a provider adapter;
3. review the execution preview and required authority;
4. approve only the exact action you intend to run;
5. inspect the resulting events, diff, and evidence.

Use `giga <agent>` for the provider's native terminal workflow, or `giga ui`
for the governed browser Workbench.

## Complete the 0.9 work-first journey

Open `/web/work` and follow `Project -> Thread -> Run -> Evidence -> Action`.
Before submission, inspect the route/model support, workspace, read-only
Effective Instructions summary, authority mode, and blockers. After submission,
review the causal run narrative and any required action in Inbox.

Preview a bounded Thread Relay delivery without mutating the target or calling
a provider:

```sh
giga session send THREAD_ID --text "review failing tests" --dry-run --json
```

Export an editor schema or an explicitly opted-in local beta report:

```sh
giga schema agent
giga evidence product-beta --project PROJECT_ID --output report.json
```

## Launch through gpt2giga

Install the optional extra, then select the reviewed route by convenience name
or immutable id:

```sh
uv tool install 'gigaloom[gpt2giga]==0.9.0'
giga --with gpt2giga --model GigaChat-2-Max codex
giga --route codex-gpt2giga-gigachat-2-max codex --help
```

The Codex/GigaChat route is a technical preview. Unknown, stale, ambiguous, or
version-drifted evidence fails before gateway/provider traffic and never falls
back to a different route.

## Next steps

- [Harness reference](harness.md) for configuration and commands
- [Work, threads, and context](work-threads-and-context.md)
- [Gateway integration](gateway-integration.md) for route support and versions
- [Agents and multi-agent behavior](agents-and-multi-agent.md)
- [Operations](operations.md) for backup and troubleshooting
- [Security](security.md) for approval, redaction, and network boundaries
