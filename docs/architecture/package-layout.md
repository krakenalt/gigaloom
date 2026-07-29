# ADR: Bounded-context package layout

Status: accepted as the T15 Phase A architecture handoff for gate `G-ARCH` on
2026-07-29.

## Context

GigaLoom is a modular monolith with durable local state, three user-facing
surfaces, built-in coding-agent adapters, provider compatibility layers, and
extension tooling. The existing Python package grew as a mostly flat namespace.
Several root modules now combine storage, application policy, provider
transport, and presentation concerns, while the largest composition and facade
files are thousands of lines long.

A big-bang move would create unnecessary compatibility and recovery risk.
Leaving package shape implicit would let parallel refactors choose conflicting
names and dependency directions. The repository therefore needs a frozen
target tree plus ratcheting checks that reject new debt without requiring the
legacy tree to become compliant in one commit.

The machine-readable policy is
`packages/gpt2giga-harness/architecture/module-budgets.json`. It is pinned to
the frozen `G0` source revision and records current root-module exceptions,
module-size ceilings, import rules, owners, and removal gates.

## Decision

The Python distribution is organized as a modular monolith by bounded context.
Top-level package names describe a product domain or an adapter surface.
Business behavior does not live in a generic global service, model, helper, or
utility package.

The final root of `gpt2giga_harness` contains only:

```text
__init__.py
entrypoint.py
py.typed
```

All other implementation belongs to one of these frozen contexts:

```text
attachments/  automation/  cli/          contracts/   core/
diagnostics/  execution/   harnesses/    integrations/ native/
projects/     providers/   review/       runtime/      sessions/
skills/       tools/       tui/          ui/
```

Directories are introduced only with real behavior and focused tests. Empty
placeholder packages are not created to make the checkout resemble the target
tree.

### Context responsibilities

| Context | Responsibility | Public boundary |
| --- | --- | --- |
| `core` | Technical primitives such as configuration, IDs, clocks, paths, redaction, serialization, instrumentation, and concurrency | Cohesive named modules; no product-context imports |
| `contracts` | Stable provider-neutral DTOs, protocols, enums, events, permissions, and serialization contracts | `contracts/*`; imports only `core`, stdlib, and typing |
| `sessions` | Conversation state, messages, runs, event streams, exports, titles, authoritative filesystem storage, and derived read models | `sessions/api.py` and explicit contracts |
| `runtime` | Durable jobs, workers, leases, approvals, outbox, side effects, revisions, and reconciliation | `runtime/api.py` and explicit contracts |
| `execution` | Provider-neutral admission, preparation, invocation, persistence, continuation, and finalization of runs | `execution/api.py` |
| `harnesses` | Adapter SDK, registry, conformance, plugins, and built-in agent adapters | `harnesses/api.py` and SDK contracts |
| `native` | Generic native-process lifecycle, discovery, snapshots, stores, and provider connectors | `native/contracts.py` and explicit lifecycle ports |
| `providers` | Provider registry, profiles, settings, accounts, authentication, normalized protocols, compatibility transports, and gateway configuration | `providers/api.py` |
| `projects` | Project configuration, memory, backup, workspace resolution, worktrees, and environment actions | `projects/api.py` |
| `attachments` | Attachment models, limits, MIME handling, rendering, and storage | `attachments/api.py` |
| `integrations` | Catalogs, installable packages, lifecycle, flows, groups, and integration SDK | `integrations/api.py` |
| `tools` | Tool contracts, profiles, policy, secrets, and managed or external MCP lifecycle | `tools/api.py` |
| `skills` | Built-in, external, and portable skills plus library, authoring, and catalog proxy behavior | `skills/api.py` |
| `automation` | Agents, workflows, schedules, evaluations, arena, and attention services | Subcontext contracts and application services |
| `review` | Provenance, reviewed evidence, artifacts, replay, promotions, handoffs, and support exports | Explicit read and reviewed-mutation contracts |
| `diagnostics` | Doctor checks, compatibility evidence, inventory, and performance tooling | Diagnostic report and export contracts |
| `cli` | Lazy command-line parsing, dispatch, errors, and output adaptation | `cli/main.py`; no business ownership |
| `tui` | Textual state, clients, controllers, projections, widgets, screens, and rendering | Application client protocols; no storage ownership |
| `ui` | FastAPI composition, dependencies, routers, projections, streaming, and packaged Cockpit delivery | Application services and transport schemas; no storage ownership |

The current `application`, `evidence`, `integration_sdk_preview`, and
`protocols` directories are temporary deviations. Their exact owners and
removal gates are recorded in the manifest. A temporary name is not permission
to create another package beside it.

### Backend target shape

The target tree uses the following internal shapes. File names can be refined
inside a context when an ADR explains the need, but the ownership boundary and
dependency direction do not change implicitly.

```text
core/
  config.py  errors.py  ids.py  clock.py  json_codec.py  paths.py
  redaction.py  security.py  instrumentation.py  concurrency.py

contracts/
  harness.py  execution.py  events.py  permissions.py  providers.py
  plugins.py  serialization.py

sessions/
  api.py  models.py  commands.py  queries.py  conversation.py
  titles.py  exports.py
  events/{models,broker,cursors,projections}.py
  storage/
    protocol.py
    filesystem/{catalog,manifests,messages,runs,events,raw_records,atomic,migrations}.py
    read_model/{sqlite,schema,revisions,rebuild}.py

runtime/
  api.py  models.py
  db/{connection,transactions,migrations,schema,diagnostics}.py
  jobs/{repository,claims,leases,retries,cancellation,payloads}.py
  workers/{service,repository,maintenance,heartbeat,wakeup}.py
  approvals/{policy,authority,repository,audit,presentation}.py
  outbox/{repository,dispatcher}.py
  side_effects/{models,repository,executor}.py
  revisions/repository.py
  reconciliation/service.py

execution/
  api.py  context.py  options.py  admission.py  preparation.py
  continuation.py  invocation.py  persistence.py  finalization.py
  preflight.py  readiness.py  routing.py
  structured/{sessions,processes,supervision}.py

harnesses/
  api.py  registry.py  plugins.py
  sdk/{contracts,conformance,scaffold,capability_matrix}.py
  builtins/
    echo.py
    direct_chat/{adapter,transport}.py
    codex/{adapter,workbench,plugin_target,mcp_target}.py
    codex/app_server/{client,protocol,process,events,session}.py
    claude/{adapter,workbench,agent_sdk,handoff,plugin_target,mcp_target}.py
    gemini/{adapter,workbench,acp,extension_target,mcp_target}.py

native/
  contracts.py  models.py  discovery.py  registry.py  snapshots.py  store.py
  process/{manager,pty,output,recovery}.py
  connectors/{codex,claude,gemini}.py

providers/
  api.py  registry.py  profiles.py  settings.py  migration.py
  accounts/{models,sessions,broker}.py
  authentication/{models,resolution,capabilities}.py
  protocols/{normalized,openai,anthropic,gemini,gigachat}/
  gateway/{proxy,preset}.py

projects/
  api.py  config.py  memory.py  bootstrap.py  preferences.py  backup.py
  workspace/{resolver,tree,files,worktrees}.py
  environment/{models,capture,commit,push,pull_request,github,editor}.py

integrations/
  api.py  models.py
  catalog/{local,federated,sync}.py
  packages/{models,installer,lifecycle,runtime}.py
  flows/{models,service,repository}.py
  groups/{models,service}.py
  sdk/{contracts,conformance,scaffold}.py

tools/
  api.py  models.py  profiles.py  policy.py  secrets.py
  mcp/{contracts,external,managed,inventory,authoring,targets}/

skills/
  api.py  builtin.py  external.py  portable.py  library.py  authoring.py
  catalog_proxy/{server,client}.py

automation/
  agents/  workflows/  schedules/  evaluations/  arena/  attention/

review/
  provenance.py  evidence.py  artifacts.py  replay.py  promotions.py
  handoffs.py  support.py

diagnostics/
  doctor/  compatibility/  inventory/  performance/

cli/
  main.py  parser.py  registry.py  context.py  errors.py  output.py
  completion.py  commands/

tui/
  entrypoint.py  app.py  contracts.py  state.py  i18n.py
  commands/  clients/  controllers/  projections/  widgets/  screens/
  rendering/

ui/
  app.py  container.py  dependencies.py
  security/  schemas/  services/  streaming/  routers/  cockpit_v2/
```

`sessions.storage` remains authoritative and transparent. A SQLite read model
is derived, disposable, and rebuildable. The same authoritative-versus-derived
distinction applies wherever a context adds an index or projection.

### Dependency direction

First-party imports follow this direction:

```text
cli tui ui diagnostics -> public application APIs
automation review      -> execution runtime sessions projects
execution              -> sessions runtime harnesses projects attachments
harnesses              -> providers native contracts
domain contexts        -> contracts core
contracts              -> core
core                   -> stdlib and approved technical dependencies
```

The diagram is a dependency ceiling, not a requirement that every lower layer
import every layer above it.

Rules:

1. `core` imports no product context.
2. `contracts` imports only `core`, stdlib, and typing.
3. Runtime and domain contexts never import `cli`, `tui`, or `ui`.
4. Cross-context imports use the target context's `api.py`, `contracts.py`, or
   another explicitly named public port. They do not reach into repositories,
   storage, routers, widgets, or other internals.
5. `harnesses` depends on neutral contracts plus provider and native public
   ports; provider modules do not import presentation surfaces.
6. `execution` coordinates public session, runtime, harness, project, and
   attachment APIs without taking ownership of their persistence.
7. `automation` and `review` consume bounded execution, runtime, session, and
   project APIs.
8. CLI, TUI, and Web adapt input and output to application APIs. They do not
   duplicate policy or query concrete filesystem and SQLite repositories.
9. `__init__.py` files remain import-light and do not form circular re-export
   chains.

The architecture checker parses source with the Python standard-library AST.
It never imports application modules. Existing internal cross-context imports
are exact temporary exceptions; each exception has an owner and removal gate,
and stale exceptions fail the check.

### Frontend target shape

Cockpit uses a small feature-oriented structure:

```text
frontend/src/
  app/{App,router,providers,queryClient,shell}/
  shared/{api,streaming,ui,hooks,lib,styles}/
  entities/{session,run,approval,environment,provider,attachment,integration}/
  features/{workbench,runs-center,integrations,settings,automation,evaluation,arena,inbox}/
  widgets/{inspector,navigation,drawers}/
  i18n/{keys,en,ru}/
  main.tsx
```

Frontend imports follow:

```text
app      -> features, widgets, entities, shared
widgets  -> entities, shared
features -> entities, shared
entities -> shared
shared   -> third-party and browser APIs only
```

One feature does not import another feature's internal file. Shared behavior
moves to a named entity or genuinely reusable shared module with its own tests.
Barrel files must not hide feature-to-feature dependencies or cycles.

### Test target shape

Tests follow the owning architecture and type of evidence:

```text
tests/harness/
  architecture/
  unit/{sessions,runtime,execution,providers,harnesses,integrations,automation,projects,cli,tui,ui}/
  contract/{adapters,api,cli,sse,storage}/
  integration/{durable_runtime,session_execution,application_surfaces}/
  migrations/{sessions,runtime}/
  performance/
  e2e/
  live/
```

Existing tests move only with their owning production context. A mass test-tree
shuffle is not a prerequisite for implementation, and `live` remains explicit
opt-in.

## Module budgets

New executable Python modules have a hard limit of 600 physical lines without
an ADR. The preferred range is 150–400 lines. A composition or compatibility
facade has a 350-line hard limit and should normally remain between 50 and 250
lines.

Additional review limits are:

| Unit | Target | Hard limit without ADR |
| --- | ---: | ---: |
| Python function or method | 10–50 lines | 100 lines |
| Python class | 50–250 lines | 400 lines |
| FastAPI router | At most 12 routes | 400 lines |
| React page or surface | 150–300 lines | 400 lines |
| React component | 50–200 lines | 300 lines |
| React hook or controller | 50–180 lines | 250 lines |
| TypeScript model or API file | 100–300 lines | 450 lines |
| CSS file | 100–300 lines | 450 lines |

Every Python module above 600 lines at `G0` has its exact physical-line count
recorded as a ceiling. It may shrink or disappear, but it may not grow. Each
structural slice reduces an owned legacy god-file by at least 15 percent while
that file remains above its hard limit. The manifest is updated downward after
the slice; raising a ceiling requires a separate ADR and measured reason.

Generated schemas and assets, frozen evidence fixtures, and migration SQL can
be exempt only through an explicit manifest entry. An exception cannot contain
executable business logic.

New generic `utils.py`, `helpers.py`, `common.py`, `misc.py`, global
`services.py`, or product-wide `models.py` modules are rejected. Splitting one
god-file into multiple files that still combine unrelated responsibilities is
not completion.

## Migration protocol

One module or cohesive responsibility moves at a time:

1. Add the destination context or subpackage together with its public contract
   and focused tests.
2. Move or extract behavior without redesigning it and without mixing in an
   algorithmic optimization.
3. Preserve the old import path with a compatibility shim of at most 30 lines.
   Larger composition or protocol facades use only their explicitly recorded
   budget.
4. Add or tighten an architecture rule so no new first-party consumer can
   adopt the legacy path.
5. Migrate consumers in small owner-specific commits through the destination
   public facade.
6. Verify entry points, dynamic imports, plugin targets, serialized type names,
   and persisted state compatibility.
7. Before deleting the shim, search production, tests, examples, and docs;
   build the installed artifact; and verify both old and new imports for the
   approved compatibility window.
8. Remove a shim only in a separate reviewed commit. If compatibility evidence
   is incomplete, retain it with an explicit owner and removal gate.

Storage migrations are repeatable after interruption. Structural moves preserve
atomic writes, locks, leases, cancellation, reconciliation, idempotency,
redaction boundaries, and authoritative JSON or JSONL state. A move never
changes `fsync`, SQLite durability, public routes, SSE events, CLI output,
provider passthrough, or approval semantics implicitly.

## Refactoring phases

| Phase | Result |
| --- | --- |
| R0 | Freeze this ADR, root namespace, import direction, ownership, temporary exceptions, and module budgets without moving production code |
| R1 | Split current choke-point files behind compatibility facades |
| R2 | Move flat root modules into bounded contexts using owner-specific commits |
| R3 | Migrate consumers to public facades and reject new legacy imports |
| R4 | Remove only compatibility shims with installed-artifact evidence; close or document remaining exceptions |

`core` and `contracts` extraction is a separately approved Phase B. It starts
only after the integration owner confirms that hot-path owners have stopped
changing the shared modules. Until that gate, `types.py`, `config.py`,
`execution.py`, `safe_paths.py`, and `instrumentation.py` remain frozen.

## Ownership during migration

| Area | Owner |
| --- | --- |
| Session storage and queries | T02 |
| Runtime DB and facade foundation | T03 |
| Runtime jobs | T04 after the runtime-foundation gate |
| Runtime workers and wakeup | T05 after the runtime-foundation gate |
| Runner and new `execution` modules | T06 |
| FastAPI composition, services, streaming, and routers | T07 |
| CLI and entrypoint | T08 |
| TUI clients, contracts, and projections | T09 |
| TUI application and rendering | T10 |
| Frontend API and query contracts | T11 |
| Frontend Workbench | T12 |
| Frontend streaming and bounded rendering | T13 |
| Architecture manifest, tests, and this ADR | T15 |
| `core` and `contracts` Phase B | T15 or T00 after explicit approval |
| Integrations, tools, and skills | T16 |
| Providers and built-in harnesses | T17 |
| Automation and review | T18 |
| Projects, workspace, and environments | T19 |
| Cross-cutting import migration, diagnostics, docs, and final cleanup | T20 |

Frozen shared contracts such as the registry and runtime policy or model
surfaces change only through the integration owner. A thread that needs another
owner's file submits an interface request instead of editing the file.

## Compatibility and rollback

This decision changes package organization, not product behavior. CLI commands,
flags, output, exit codes, REST paths, response shapes, SSE events and cursors,
plugin entry points, provider-native passthrough, persisted state, and TUI or
Web semantics remain compatibility contracts.

Each structural commit is independently revertible. Compatibility shims and
the authoritative state formats provide the rollback boundary. Derived indexes
can be deleted and rebuilt; authoritative user state cannot be discarded to
make a package move easier.

## Consequences

Parallel refactors can use frozen context names and ownership without creating
new namespace conflicts. New modules are bounded immediately, while legacy
god-files and internal imports can only stay level or shrink.

The cost is explicit facade design, temporary shims, a maintained exception
manifest, and extra installed-artifact checks before cleanup. That cost is
preferred to hidden cross-context coupling, broad consumer churn, or a
high-risk big-bang rewrite.
