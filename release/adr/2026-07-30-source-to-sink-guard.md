# ADR: Source-to-Sink Guard

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

Web, MCP, attachments, repository content, terminal output, and model-generated
content may influence a requested side effect. Existing approvals answer who
may act on a concrete target; they do not prove that an untrusted payload is
safe to send to a privileged sink.

## Decision

GigaLoom adds a versioned, deterministic data-flow admission boundary with:

- `SourceRef`;
- `ProvenanceClass = user | repo | web | mcp | attachment | terminal | generated`;
- `TrustClass = trusted | bounded | untrusted | unknown`;
- `Sensitivity = public | internal | secret`;
- `InfluenceSet`, `SinkRequest`, `SinkDecision`, and `DataFlowReceipt`.

The guard runs before external side effects and before ordinary authority
approval can be consumed. It canonicalizes the exact destination, binds source
and payload digests, creates a bounded non-secret preview, and returns a stable
reason. Unknown provenance, ambiguous redirects, cross-destination rebinding,
and secret-bearing payloads fail closed.

The decision is pure policy code over a bounded request. It makes no model call
and does not claim to solve prompt injection. Terminal and generated content
are untrusted influence and can never create authority. A successful guard
decision is necessary but not sufficient: the existing network, GitHub, MCP,
approval, and sandbox owners still enforce their scopes.

## Owner

W3 owns provenance/trust contracts, deterministic admission, source adapters,
protected sink adapters, and data-flow receipts. Existing runtime authority,
network, GitHub, MCP, secret, and approval modules remain the side-effect
owners. UI layers may render decisions but cannot recompute or override them.

## Migration

New source-bearing records receive schema version, provenance, trust,
sensitivity, and digest at ingress. Existing records without provable
provenance migrate as `unknown`; they are readable but cannot authorize a
protected sink. No content is retrospectively promoted from a string label.

Protected adapters are enabled source by source and sink by sink. The first
release admits only the explicitly listed bounded slice.

## Rollback

Disabling the guard disables the guarded external integration or forces an
explicit safe/manual path; it never bypasses admission. Existing receipts stay
readable and immutable. A schema version unknown to the running code blocks the
decision rather than falling back to legacy behavior.

Rollback cannot turn terminal output, model output, or an old approval into
trusted provenance.

## Redaction and privacy

Receipts contain normalized destination metadata, source classes, reviewed
digests, bounded non-secret previews, decision, and stable reason. Secrets,
full payloads, auth headers, cookies, raw terminal output, attachment bodies,
and model prompts are excluded. Secret detection itself is represented by a
classification and digest, not the secret value.

Public compatibility responses are not blanket-redacted by this guard; only
storage, diagnostics, evidence, admin preview, and protected sink boundaries
use its redacted projection.

## Compatibility

The guard is additive to existing approval and sandbox contracts and does not
change public OpenAI-, Anthropic-, Gemini-, or GigaChat-shaped responses.
Source records without the new schema remain readable but are not admissible
for new guarded writes.

Stable reasons and versioned receipts are the compatibility surface. Unknown
enum or schema values fail closed.

## Bounded 0.6 slice

The first slice covers one Web result, one MCP result, one attachment,
terminal/generated influence, one external URL/network sink, and one
GitHub-or-MCP write sink. It includes destination normalization and receipt
projection. It excludes generic taint tracking, arbitrary code analysis,
model-based classifiers, and a claim of universal exfiltration prevention.

## Hermetic acceptance matrix

| Fixture | Required result |
| --- | --- |
| Web/MCP/attachment indirect-injection payload | Influence retained; privileged sink denied |
| Safe exact user-authored destination and payload | Admission succeeds with bounded receipt |
| Unknown provenance | Fail closed |
| Secret in payload or metadata | Block without storing secret |
| URL case/default port/path normalization | One canonical destination |
| Ambiguous or cross-origin redirect | Revalidate or deny |
| Destination changed after approval | Digest mismatch and deny |
| Terminal/model output requests authority | Remains untrusted influence |
| Duplicate deterministic request | Same decision and reason |
| Admission performance fixture | p95 at or below 5 ms |
| Model service unavailable | No effect; no model call exists |
| Unknown schema/enum | Fail closed |
