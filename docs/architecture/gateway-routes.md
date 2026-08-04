# Gateway routes

Status: accepted on 2026-08-04.

## Context

GigaLoom needs to launch coding agents through an exact public `gpt2giga`
artifact without confusing agent, protocol, gateway, model, and route
identities. A convenience command must not become implicit fallback or a second
process supervisor, and launch preparation must not modify provider-native
configuration.

## Decision

### Identity and command grammar

`agent_id`, client protocol, `gateway_id`, public model alias, and immutable
`route_id` are separate identities. The canonical form is
`giga --route <route-id> <agent-id>`. The convenience form
`giga --with <gateway-id> --model <model> <agent-id>` must resolve to exactly
one immutable route before spawning. Non-interactive ambiguity is an error.

Global GigaLoom options end at the agent-id token. All later tokens are opaque
native-agent arguments: `giga --model X codex` selects a route model, while
`giga codex --model X` forwards the option to Codex. Dry-run JSON is redacted
and causes no gateway or provider traffic. Missing capability never falls back
to another protocol, provider, model, or agent.

### Public contracts

`GatewayProfileV1` binds gateway identity and display name, managed/external
mode, package or executable identity, exact version window, base URL,
startup/health/readiness/models/capability revisions, secret and TLS policy
references, and artifact/profile digest.

`BridgeRouteV1` binds route and agent ids, client protocol, gateway profile,
public alias, upstream provider/model facts reported by the gateway, capability
and loss-matrix revisions, support status, and any reasoning selector or
required acknowledgement.

`LaunchOverlayV1` binds the resolved route to a GigaLoom-managed home, redacted
environment delta, generated configuration references, existing process lease,
preflight receipt, and gateway capability digest. Secret values never enter a
profile, route, overlay, receipt, log, or dry-run plan; only references cross
the launch boundary.

### Lifecycle and compatibility truth

An `external` profile preflights an already-running gateway. A `managed`
profile acquires the existing GigaLoom process lease, starts the exact public
artifact when necessary, waits for readiness, and binds process evidence to the
run. Cancellation, recovery, redaction, and managed roots keep their current
owners; there is no second daemon supervisor.

Support status has exactly four values:

- `stable`: pinned agent window, gateway matrix cell, and end-to-end evidence
  are all green;
- `technical_preview`: tested with bounded limitations or elevated drift;
- `vendor_unsupported`: technically compatible wire path outside agent-vendor
  support;
- `blocked`: endpoint/injection is unsupported or required semantics are lost.

GigaLoom never promotes gateway or adapter evidence. For the released
`gpt2giga 0.3.0` artifact, Codex Responses to GigaChat is
`technical_preview` because normalized Responses parity is incomplete. The
profile binds the public wheel/version/digest and uses only the installed CLI
and HTTP contracts; importing protocol or provider models from private
`gpt2giga` modules is forbidden. Unknown or stale startup, models, capability,
loss-matrix, artifact, or adapter revisions fail closed until revalidated.

### Overlay boundary

Generated configuration lives only under a GigaLoom managed root or bounded
temporary directory. Launch and cleanup never modify `~/.codex`, `~/.claude`,
`~/.gemini`, another provider-native home, or global provider configuration.
Codex uses a Responses-wire overlay; Chat Completions fallback is forbidden.

## Migration and rollback

Profiles and routes are additive, versioned records. Migration resolves legacy
explicit gateway choices into reviewed routes only after exact capability and
artifact validation; absence of a match is a visible blocked result. Rollback
disables route admission, releases the existing lease, and removes only the
managed overlay. It does not edit native homes, stop unrelated external
gateways, or silently reinstate a legacy/fallback route.

## Consequences

Every launch is explainable by immutable route and artifact evidence. Managed
and external gateways share existing lifecycle owners, while secrets and native
configuration stay outside persisted launch state.
