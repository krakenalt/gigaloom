# ADR: Native-agent terminal ownership and dynamic profiles

Status: accepted for GigaLoom 0.7 roadmap slice P0-01 on 2026-07-31.

## Context

GigaLoom 0.6 has two terminal ownership models that cannot remain true at the
same time. The root dispatcher can open a GigaLoom-owned Textual Workbench,
while provider namespaces can be classified into native passthrough, managed
handoff, or a structured Workbench takeover. The latter model recognizes a
fixed set of Codex, Claude, and Gemini namespaces and represents a human launch
with `TuiLaunchIntent`, which combines provider, model, project, session,
policy, and presentation concerns.

That boundary prevents a fourth coding agent from being added declaratively.
It also makes the meaning of `giga <agent>` depend on structured adapter
version evidence even though the user selected the provider's native terminal
surface.

GigaLoom 0.7 needs one stable distinction: native commands are human-owned
provider processes, while governed runs are explicit GigaLoom structured
execution. The existing browser Workbench and managed terminal kernel remain
valuable, but neither may replace the provider CLI selected by a native agent
command.

## Superseded decisions

This ADR supersedes the following accepted decisions where they conflict with
the replacement contract below:

| Previous decision | Superseded part | Replacement |
| --- | --- | --- |
| [`docs/architecture/provider-native-cli-facade-adr.md`](../../docs/architecture/provider-native-cli-facade-adr.md) | Exactly three root provider namespaces; L2 `structured_workbench` takeover of known human forms; Textual as the only GigaLoom-owned human frontend | Agent ids and aliases come from declarative profiles; `giga <agent>` always launches that agent's native CLI; Web is the only GigaLoom-owned interactive Workbench |
| [`release/adr/2026-07-30-managed-tmux-terminal-boundary.md`](2026-07-30-managed-tmux-terminal-boundary.md) | Textual suspend/handoff as the local attach transition | Local attach enters the real provider process through the managed terminal kernel without a GigaLoom terminal frontend |
| [`release/adr/2026-07-30-native-codex-app-server-resume-compact.md`](2026-07-30-native-codex-app-server-resume-compact.md) | Any implication that structured Codex admission selects or replaces the human `giga codex` surface | `giga codex` remains the real Codex CLI in direct or managed native mode; app-server is a separate structured route used only by an explicit governed surface |
| The `TuiLaunchIntent`, `ConsoleSurface.TUI_HUMAN_WORKFLOW`, and `initialize_textual` dispatch model | One presentation-oriented contract owns command intent, execution hints, and Textual initialization | Native invocation, agent resolution, native launch, project launch, and structured route decisions use separate contracts owned by their bounded contexts |

The native process parity, secure executable resolution, privacy, managed
terminal lifecycle, Web attach, and fail-closed structured capability rules in
the earlier ADRs remain accepted unless this ADR explicitly changes them.
Historical release notes and ADR text remain historical evidence; they are not
current 0.7 product behavior.

## Decision

### Terminal product boundary

GigaLoom no longer owns a terminal UI. Textual and every GigaLoom fullscreen,
raw-mode, curses, prompt-toolkit, or replacement terminal Workbench are outside
the product boundary.

The terminal surfaces are:

- `giga` prints a bounded plain-text launcher summary and performs no runtime
  migration, network access, compatibility probe, or process launch;
- `giga --help` and `giga --version` remain static, import-light metadata
  paths;
- `giga ui` starts the browser control plane and Workbench;
- `giga <agent-id-or-alias> [opaque suffix...]` launches the real native agent
  CLI;
- `giga agent`, `giga project`, `giga route`, `giga run`, and `giga session`
  are ordinary CLI/admin surfaces with explicit machine-readable modes;
- governed interactive chat belongs to Web or an explicit structured run, not
  to a GigaLoom terminal chat.

Web is the only GigaLoom-owned interactive Workbench. It owns projects, launch
profiles, route inspection, approvals, evidence, MCP Apps, and managed-terminal
attach. It does not own or reinterpret provider-native screen state.

### Native and structured execution are disjoint

The first agent token commits the invocation to the native agent surface. The
entire remaining argument vector is provider-owned and is forwarded
token-for-token. GigaLoom does not parse, translate, inject, remove, persist,
or hash provider flags, prompts, stdin, stdout, or stderr.

Native launch has two implementation modes:

- `direct_native` uses the reviewed exec/inherited-console parity contract;
- `managed_native` runs the same exact provider command in the existing
  managed terminal kernel so local and Web clients can attach to one process.

Managed mode may be selected only from affirmative human-form matchers, human
TTY topology, platform capability, and managed-terminal policy. Help, version,
completion, JSON/JSONL, headless, protocol, admin, piped, redirected, CI,
unknown, or potentially machine-readable forms stay direct. Structured adapter
availability or version drift never selects, blocks, or replaces a native
invocation.

Structured execution is entered only through an explicit governed owner such
as `giga run`, Web, workflow, or schedule. It may use app-server, ACP, SDK, or
another admitted machine-readable adapter. It has its own capability,
authority, compatibility, session, evidence, and failure contracts. A native
session and a structured session are not the same session without separately
proven exact binding.

There is no silent native-to-structured conversion, structured-to-native
fallback, mid-run agent switch, or provider-specific takeover of the root
namespace.

### Declarative agent namespace

An Agent Profile is the single user-facing agent identity source. Built-in
Codex, Claude, and Gemini support is expressed by declarative profiles rather
than root-parser branches. Later profiles, including Pi, pass through the same
resolution path.

Each profile may declare:

- one stable agent id and bounded aliases;
- zero or one native launch specification;
- zero or more separately identified structured route references;
- source, trust, compatibility, platform, and digest evidence.

Presence or discovery is not admission. Registry metadata does not grant
execution, installation, authentication, network, filesystem, or provider-home
authority. GigaLoom never auto-installs an agent or converts registry package
metadata directly into a shell command.

Root resolution order is frozen as:

1. root metadata;
2. a reserved core command from the canonical CLI registry;
3. an exact registered agent id;
4. an exact registered alias;
5. a typed unknown-command-or-agent result with bounded suggestions.

Core commands always win. Profile ids and aliases cannot shadow core commands,
built-in ids, other ids, or other aliases. P0-02 must derive the collision
fixture from the real CLI registry so there is no manually synchronized second
core-command list.

### Replacement contracts and ownership

P0-02 freezes immutable shared contracts for agent profiles, native launch
specifications, structured route references, native invocations, resolution
results, and stable reason enums.

Ownership is split as follows:

- `harnesses.agent_profiles` owns agent identity, declarative sources, and
  structured route references;
- `native.launch` owns executable resolution and direct/managed process launch;
- `cli` owns root command resolution and calls only public application APIs;
- `projects` owns project and launch-profile hints;
- `execution` owns structured route admission and recommendation;
- `ui` consumes public contracts and owns no provider process grammar.

`TuiLaunchIntent` is retired rather than renamed or moved. Project/profile
hints cannot become hidden arguments to direct `giga <agent>`. Structured
route requirements cannot become native launch policy.

### Authority, privacy, and failure semantics

Executable discovery resolves and pins a reviewed absolute file identity,
rejects recursion and unsafe targets, and never uses a shell string. Direct and
managed launch preserve provider-owned cwd, allowed inherited environment,
stdio, signals, and exit semantics according to the existing POSIX and Windows
contracts.

Native launch stores no raw arguments or content. Managed mode may retain only
content-free lifecycle metadata and explicitly allowed bounded terminal
evidence. Detach is not exit, reconnect does not relaunch, and Web attach is
authorized against the same managed terminal owner.

Missing or unsafe executables fail before provider side effects with typed,
content-free diagnostics. Unknown native syntax remains provider-owned and
direct when the target is safe. Unknown or stale structured capability fails
closed only on the explicit structured surface.

## Migration and rollback

P0 replaces dispatch before removing Textual. Transport-neutral application
services currently consumed by Textual move only to their canonical owners;
Textual controllers and presentation state do not migrate into generic
services. Sessions, runs, approvals, evidence, native bindings, and the managed
terminal kernel remain intact.

Textual-only preferences are retired through the release migration receipt and
are not silently converted into Web preferences. The old `giga tui` and
interactive `giga chat` paths receive no compatibility aliases.

Before P0 closes, rollback may return to the complete 0.6 release line. Within
0.7 there is no feature flag that restores Textual or structured takeover of a
native command. If managed-terminal capability is unavailable, native launch
degrades visibly to `direct_native`; if a structured route is unavailable, the
explicit governed run fails without changing the native surface.

## Consequences and next gate

Adding a coding agent no longer requires a root parser branch or a terminal UI
change. Native CLI compatibility can outlive structured adapter version drift,
while governed routes retain explicit capability and authority admission. Web
remains the product's rich operator surface and reuses the existing managed
terminal lifecycle without claiming ownership of provider-native UI state.

This P0-01 slice changes architecture only. It does not change runtime
dispatch, dependencies, package contents, user state, provider homes, or
external systems. P0-02 must freeze the shared contracts before P0-03 changes
root dispatch; P0-04 then removes Textual, and P0-05 adds regression gates.
Wave A cannot start until the complete P0 closure gate is green and its exact
`P0_GREEN_SHA` is recorded.
