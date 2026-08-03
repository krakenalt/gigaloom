# ADR: Work-first composition and bounded Thread Relay

Status: accepted on 2026-08-04 for the GigaLoom 0.9 foundation.

## Context

GigaLoom already owns projects, structured sessions, runs, evidence, actions,
authority decisions, and native processes. Version 0.9 needs to present those
owners as one work narrative and let a user read or address another supported
thread without creating a second transcript store or an autonomous
agent-to-agent network.

## Decision

### Work-first composition

The primary product path is `Project -> Thread -> Run -> Evidence -> Action`.
The Work screen composes existing owners and has one primary action: send the
user outcome to the selected thread. Before dispatch it shows project/thread,
agent, route/model support, workspace, effective-instructions summary,
authority mode, and blockers. After dispatch it shows one causal run narrative.

This is a vocabulary and projection change. Project, session, run, evidence,
action, approval, route, and process records remain with their current owners.

### Thread Relay contracts

`ThreadLocatorV1` identifies `source_kind` (`gigaloom`, `codex`, or `acp`),
adapter, project, thread, actor scope, capability revision, and optional
workspace/provider references. `ThreadReadProjectionV1` contains bounded visible
messages, title/status/time, active-turn and route/model facts, relationships,
cursor, omissions, redaction, and unsupported facts.

`ThreadMessageEnvelopeV1` contains source and target locators, actor/project
binding, the fixed `user` role, `user_authored` or
`agent_proposed_user_approved` mode, message/attachment references, intent,
expected target/active-turn revisions, idempotency key, expiry, and depth.
`ThreadDeliveryReceiptV1` records identities, action/status, timestamps,
run/job/turn references, capability revision, content digest only, and a
bounded failure, expiry, or cancellation reason.

### Adapters and authority

GigaLoom sessions use their durable structured-session owner. Codex access is
limited to a pinned app-server method/version capability. ACP access is limited
to advertised session capabilities. Opaque native terminals expose no foreign
history; only an already GigaLoom-owned live terminal can receive input through
its existing authority path.

The restricted agent surface contains only `thread.list`, `thread.read`,
`thread.send`, and `thread.status`. Settings, secrets, installation,
publication, recovery mutation, raw history, system injection, hidden
reasoning, and provider-private state are outside the contract.

### Safety and bounds

- Actor and project bindings are mandatory; cross-project access is denied by
  default.
- Delivery TTL and idempotency key are mandatory. Relay depth is at most one,
  and a source run/thread may have at most four outstanding child deliveries.
- A mutating delivery checks the expected target revision. Steering also
  requires the exact active-turn id.
- Only the `user` role can be delivered, and an agent proposal requires an
  explicit user approval receipt.
- Reads and cursors are bounded. Attachments remain references with availability
  and omission facts. Receipts persist content digests, not message content.
- Failed delivery never rewrites target history. No automatic loop, whole
  transcript copy, hidden-state portability, or silent memory mining exists.
- Unknown or stale capability revisions deny read/send admission until explicit
  revalidation; they never select a fallback adapter.

## Migration and rollback

The four versioned contracts are additive projections over existing owners.
Enabling an adapter requires an explicit capability revision; existing sessions
are not imported or rewritten. Rollback disables the adapter and new delivery
admission while preserving immutable receipts and all source-owned session
state. No vendor home is read for migration or changed during rollback.

## Consequences

The product can compose work and cross-thread actions without duplicating
session truth. Relay remains bounded, attributable, reversible at the adapter
boundary, and incapable of silently escalating project or provider authority.
