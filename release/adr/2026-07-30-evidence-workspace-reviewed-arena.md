# ADR: Evidence Workspace, Action Inbox, and Reviewed Arena composition

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

Evidence, pending operator actions, terminal recovery, and candidate comparison
currently span several owners. Building a second workflow, approval, or review
engine would duplicate authority and create inconsistent facts.

## Decision

The Operator Evidence Workspace is a bounded per-run projection over existing
owners. It references, by stable identifiers and immutable digests:

- run/candidate/gate/findings state;
- changed files and base/patch digests;
- `ContextManifest`;
- Source-to-Sink receipts;
- cost knowledge and leases;
- terminal/native session state;
- omissions, staleness, and exact next actions.

The cross-run Action Inbox projects existing approvals, automation questions,
MCP elicitation, provider-login status, and durable runs waiting for input.
Each item has a typed kind/schema, owner, workspace, origin, revision/digest,
consequence, expiry, and idempotency identity. Responses call the existing
application owner. Duplicate responses are idempotent; stale, cross-owner, and
cross-workspace responses fail closed.

Reviewed Arena composes those same owners:

- exactly two candidate runs;
- separate worktree, native home, terminal, provider session, and cost lease
  per candidate;
- one required deterministic project gate per candidate;
- immutable `CandidateEvidence` referencing Run Capsule and ContextManifest
  digests;
- one reviewer over immutable file-backed evidence;
- closed outcomes `selected`, `needs_human`, `no_eligible_candidate`,
  `review_failed`, and `canceled`;
- only `Review winner`, which enters the existing manual review/apply flow.

Gate failure makes a candidate ineligible before model ranking.
`automatic_apply=false` is an invariant, not a UI default.

## Owner

W6 owns Evidence Workspace and Inbox projections, bounded APIs, event/resnapshot
protocol, and Web/TUI presentation. The existing approval, automation, MCP,
provider-login, runtime, session, terminal, and review services retain their
facts and mutation authority.

W5 owns Arena candidate evidence, exactly-two execution, eligibility,
arbitration, leases, isolation, and manual promotion handoff. The integrator
owns shared public DTO boundaries between W5 and W6.

## Migration

Existing runs receive partial projections from facts that can be resolved by
identifier and digest. Missing context, cost, trust, terminal, or capsule
evidence is explicitly unavailable; it is never reconstructed or copied into a
new mutable store.

Inbox adapters are added owner by owner. Existing pending actions keep their
current owner and lifecycle. Arena creates new run/candidate records and does
not reinterpret legacy multi-run experiments as reviewed candidates.

## Rollback

Workspace and Inbox UI/API projections can be disabled without deleting owner
records or pending actions; operators retain the existing owner-specific
surfaces. Arena creation can be disabled while existing candidates and
evidence remain inspectable and cancelable through their owners.

Rollback never auto-selects, auto-applies, collapses two worktrees, or bypasses
stale/owner checks. A reviewer failure resolves to `review_failed` or
`needs_human`.

## Redaction and privacy

Projections contain identifiers, states, bounded summaries, classifications,
relative paths, and reviewed digests. OAuth credentials, provider auth, raw
terminal output, secret values, complete prompts/responses, socket paths, and
candidate private-home contents are excluded.

Provider-login items expose status/continue/cancel, never OAuth secrets as form
payload. Candidate isolation prevents access to another candidate's home,
socket, thread id, worktree, or artifacts.

## Compatibility

Web and TUI consume the same application APIs. Existing approvals, automation,
runtime, session, review/apply, and provider-login semantics remain
authoritative. Event reconnect returns an explicit resnapshot boundary instead
of implying lossless infinite history.

Unknown item kinds, action revisions, evidence schemas, or arbitration states
fail closed. The only promotion path is the existing manual review flow.

## Bounded 0.6 slice

The slice covers one per-run workspace, a minimal cross-run inbox, exactly two
Arena candidates, one deterministic gate each, one reviewer, immutable
evidence, and manual winner handoff. It excludes a second workflow engine,
more than two candidates, multiple review rounds, automatic apply, generic
provider routing, and unbounded history.

## Hermetic acceptance matrix

| Fixture | Required result |
| --- | --- |
| Large evidence/inbox set | Bounded pagination and filters |
| Event reconnect after gap | Explicit resnapshot and stable cursor |
| Duplicate action response | Idempotent owner result |
| Stale revision/digest | Conflict before mutation |
| Cross-owner/workspace response | Forbidden |
| Provider login item | Status/actions only; no OAuth secret payload |
| One candidate gate fails | Ineligible before reviewer ranking |
| Both gates fail | `no_eligible_candidate` |
| Reviewer failure or malformed result | Never accidental selection |
| Tie / insufficient evidence | `needs_human` |
| Stale base / protected path / dirty destination | Promotion blocked |
| Candidate cancellation | Bounded canceled outcome and receipts |
| Unknown cost under finite budget | Spawn/selection blocked by policy |
| Candidate isolation probes | No home/socket/thread/worktree/artifact leak |
| Selected candidate | `Review winner` only; no apply side effect |
| Web and TUI projection | Same owner-backed DTOs and decisions |
