# ACP provider bridge and gateway simplification

Status: accepted for GigaLoom 0.9.1 on 2026-08-04.

## Context

ACP stdio connects GigaLoom to an agent process. The agent reaches its model
provider over a separate HTTP protocol. An ACP-capable agent therefore does not
automatically support an OpenAI-compatible endpoint, a custom base URL, or an
arbitrary model alias.

GigaLoom 0.9.0 has two incompatible interpretations of these facts. Gateway
discovery correctly maps an OpenAI Chat Completions route to the ACP consumer,
but generic injection recognizes ACP only when the route protocol itself is
named `acp`. The managed ACP path bypasses that injection and keeps an
OpenCode-only capability table in a separate module. Extending either path
would preserve the ambiguity and duplicate route and provider facts.

## Decision

### Bridge placement

Provider configuration is part of the existing managed ACP launch lifecycle.
GigaLoom will not add an ACP proxy daemon. A proxy would have to duplicate ACP
session, cancellation, permission, streaming, MCP, authentication, and process
lifecycle behavior while still being unable to make an agent accept an
unsupported provider endpoint.

The launch sequence remains owned by GigaLoom:

1. resolve one current gateway route;
2. prepare an isolated process environment;
3. start and initialize the ACP agent;
4. apply a verified provider strategy;
5. create the ACP session and select the model;
6. run the prompt and clean up the owned process state.

No strategy may modify a native agent home.

### Separate transport and provider protocol

The route plan keeps these dimensions distinct:

- `transport_kind`: how GigaLoom communicates with the agent, such as
  `acp_stdio_v1`;
- `provider_protocol`: how the agent communicates with the selected gateway,
  such as `openai_chat_completions` or `openai_responses`.

An ACP consumer must be able to accept a route whose provider protocol is
OpenAI Chat Completions. It must not require the provider protocol to be named
`acp`.

### One resolved route

One internal immutable `ResolvedGatewayRoute` carries the route id, gateway id,
provider protocol, credential-free base URL, public model alias, support
status, capability digest, and reason ids. The credential remains a separate
runtime dependency.

One resolver produces this object or a typed refusal for native launch,
managed ACP, CLI dry-run, and Web preflight. Presentation surfaces do not
interpret model or capability payloads independently. Existing external JSON
contracts remain stable until their normal versioned migration.

### Capability-first provider configuration

Provider strategies are attempted in this order:

1. ACP `providers/*` methods advertised during `initialize`;
2. an exact built-in adapter matched by registry id and bounded version policy;
3. an explicit native-only result.

The built-in strategies are ACP provider methods, verified OpenAI environment
variables, and verified ephemeral configuration. Unsupported is a resolution
result, not a fourth strategy. Unknown agents never receive a blind OpenAI
environment fallback.

If an agent advertises provider methods but list, set, or readback validation
fails, launch is blocked. GigaLoom does not silently fall back to an adapter,
because that would hide a contract regression. Selecting a gateway model for a
native-only agent is also blocked before session handoff; launching that agent
with its native provider remains available.

### Gateway capability ownership

gpt2giga owns provider and model capabilities. GigaLoom consumes its public
inspection, `/models`, and `/bridge/capabilities` contracts and does not infer
capabilities from model names or maintain a second model catalog.

Normal managed admission uses the compatible version window plus public
machine-contract revisions and observed installed-artifact evidence. An exact
source artifact digest is not the sole compatibility rule for a known packaged
gateway. Unknown executables retain the stricter local-artifact path.

### Persistence and secrecy

Onboarding may persist a content-free provider-bridge projection in the
existing installation record. It may contain strategy, supported protocols,
adapter id and revision, model-selection mode, status, and reason ids. It must
not contain credentials, header values, generated configuration, user paths,
provider content, or prompts.

The launch route contains only a credential-free URL. Secrets are resolved at
the process or transport boundary and never enter route plans, receipts, logs,
diagnostics, or persisted probe state.

## Simplification boundary

The new bridge replaces the OpenCode-only managed gateway path; it does not run
beside it. After migration:

- the OpenCode-only managed gateway module is removed;
- generic native injection no longer generates an ACP-only selector;
- there is one gateway route resolver and one ACP provider bridge resolver;
- no new daemon, persistent store, or mandatory runtime dependency is added;
- at most two production modules are added for the ACP provider bridge;
- production lines in the scoped gateway and ACP paths do not exceed the
  recorded 0.9.1 baseline.

Broader CLI, session, frontend, and evidence-framework rewrites are outside the
patch release.

## Migration and rollback

Old onboarding records without provider-bridge facts remain readable and show
that a new probe is required. Probe or update fills the additive projection
without creating a session or contacting a model provider.

Rollback is selection-based: omit the gateway choice and the agent uses its
native provider. Ephemeral configuration disappears with the managed process
or temporary root. No user configuration needs restoration because none is
modified.

If contract-based gateway admission regresses, narrow the compatible version
window while keeping observed artifact evidence. Do not restore a hard-coded
source digest as the normal admission mechanism and do not introduce a second
resolver.

## Consequences

Supported ACP agents can use gateway routes without manual native
configuration, while unsupported agents remain honestly native-only. Route
facts have one owner, provider capability failures are visible before provider
traffic, and the launch lifecycle stays within existing process and security
boundaries.
