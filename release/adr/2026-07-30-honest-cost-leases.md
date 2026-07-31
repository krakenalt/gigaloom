# ADR: Honest cost observations and budget leases

Status: accepted for GigaLoom 0.6 roadmap slice P0-02 on 2026-07-30.

## Context

Tokens, subscription quota, and monetary cost are different dimensions.
Native subscription-backed Codex normally has unknown monetary cost, not zero.
Fan-out and Arena cannot safely enforce finite budgets if unknown values are
silently converted to free usage.

## Decision

GigaLoom adds versioned contracts for:

- `BudgetPolicy = unlimited | finite(currency, amount)`;
- `CostConfidence = exact | estimated | unknown`;
- `CostObservation`;
- `TokenObservation`;
- `SubscriptionQuotaObservation`;
- `BudgetAdmission`;
- `BudgetLease`;
- `CostReceipt`.

Money, tokens, and quota remain separate dimensions and units. Unknown is a
value with provenance and reason; it is never formatted, compared, summed, or
persisted as zero.

The first enforcement slice covers only explicitly API-priced routes with an
admitted price source. Finite monetary policy requires pre-spawn admission.
A parent transaction atomically reserves headroom and issues bounded child
leases. The server enforces the lease ceiling during execution and closes it
with a final receipt for success, failure, or cancellation.

Native subscription routes record available token/quota observations and
monetary `unknown`. Under a finite monetary budget they are blocked unless a
future separately reviewed policy explicitly admits unknown cost; default 0.6
behavior does not.

## Owner

W5 owns cost/budget contracts, price-source admission, atomic lease storage,
pre-spawn enforcement, and final receipts. Provider adapters supply
observations with provenance but cannot invent prices. Runtime and Arena
consume public admissions/leases and cannot bypass them.

## Migration

Existing usage records migrate as observations only where unit, amount,
confidence, and provenance are known. Missing monetary evidence becomes
`unknown`; it is never backfilled as zero. Existing token counts remain token
observations and are not converted to money without an admitted price source.

Fan-out paths adopt leases incrementally. A route requiring finite enforcement
is disabled until its lease path is complete.

## Rollback

Rollback disables finite-budget fan-out or the affected priced route; it never
executes without admission. Unlimited single-run behavior may continue with
honest observations. Open leases are reconciled into bounded terminal receipts
and cannot be reused after rollback.

Removing a price source changes future confidence to unknown and blocks new
finite admissions; it does not rewrite historical receipts.

## Redaction and privacy

Cost records include provider/model route class, units, amount when known,
confidence, price-source/version digest, lease identity, timestamps, and
bounded reasons. They exclude credentials, account tokens, billing identifiers,
payment data, raw prompts/responses, and secret provider metadata.

Budget failure never triggers credential or account switching.

## Compatibility

Existing usage/token projections remain readable but must adopt explicit units
and confidence before participating in monetary admission. UI and API clients
must render unknown distinctly from `0`.

Unknown currency, unit, confidence, price schema, or lease revision fails
closed for finite admission. Unlimited policy is explicit, not an omitted
budget value.

## Bounded 0.6 slice

The slice covers exact/estimated/unknown observations, finite/unlimited policy,
explicitly priced API routes, atomic parent-to-child leases, server-side
headroom, final receipts, and Arena pre-spawn admission. It excludes provider
billing reconciliation, subscription price inference, account routing, credit
purchase, and multi-currency conversion.

## Hermetic acceptance matrix

| Fixture | Required result |
| --- | --- |
| Exact, estimated, unknown round trip | Confidence and provenance preserved |
| Unknown UI/API value | Never rendered or serialized as zero |
| Explicit API price inside finite headroom | Lease admitted |
| Request exceeds headroom | Denied before spawn |
| Unknown monetary route under finite policy | Denied by default |
| Unlimited policy | Explicit admission without invented cost |
| Child exceeds lease | Server-side block |
| Concurrent lease contention | Atomic; total never exceeds parent |
| Failure or cancellation | Bounded final observation and closed lease |
| Arena two-child spawn | Both leases reserved before either unbounded run |
| Price source changes | Digest mismatch and re-admission |
| Budget failure | No credential/account switching |
| Lease transaction performance | p95 at or below 5 ms |
