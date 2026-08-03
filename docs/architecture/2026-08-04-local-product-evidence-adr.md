# ADR: Local, content-free product evidence

Status: accepted on 2026-08-04 for the GigaLoom 0.9 foundation.

## Context

A design-partner beta needs evidence about whether users reach successful,
recoverable work. Building telemetry or inferring intent from filesystem
timestamps would violate the local product boundary and produce weak metrics.
GigaLoom already owns durable run, action, approval, recovery, route, process,
and evidence facts.

## Decision

### Owner and command

The existing evidence owner produces `ProductEvidenceReportV1` through the
explicit local command
`giga evidence product-beta --project <id> --output <path>`. Export is opt-in,
project-scoped, bounded, deterministic for the same source revisions, and local
only. Creating a report performs no upload, network export, account lookup,
outreach, dependency execution, or provider call.

### Report contract

When existing owner facts support them, the report computes durations and
outcomes for first project to successful run, project to accepted change,
first-run success or truthful blocker, approval latency/abandonment,
intervention/cancellation/recovery/resume, review to accepted change, repeated
Work/Inbox/Automations/Library use, Relay adoption/rejection, gateway preflight
or unsupported-route rate, and managed sidecar cold/warm attach.

Every metric binds its schema/version, project pseudonymous identity, bounded
observation window, source owner revisions/digests, numerator/denominator or
event count, outcome, and omission/unknown reasons. `unknown` remains unknown:
the report does not infer install time, user intent, success, or adoption from
weak filesystem proxies.

### Privacy and compatibility

Evidence is content-free. It excludes prompts, responses, transcript text,
code, diffs, unrestricted repository paths, raw arguments, credentials,
account material, attachment bodies, and provider payloads. It may contain
enumerated outcomes, counts, bounded durations, redacted classifications, and
digests already owned by GigaLoom evidence contracts.

No new event, session, process, route, approval, or evidence store is created.
Unknown or stale source revisions make affected metrics `unknown` and record an
omission; they never borrow facts from another owner. Export success does not
mean a dependency, route, or launch succeeded.

The pilot contract is a separate dated opt-in document defining cohort,
privacy/support boundary, exact metrics, stop criteria, and one decision from
`continue`, `narrow`, `internal_only`, `defer`, or `stop`. Outreach or access to
someone else's data always needs separate authorization.

## Migration and rollback

The report is a derived, versioned artifact over existing immutable facts.
Migration recomputes a new report version without backfilling invented events.
Rollback disables/deletes only user-selected report files; it does not mutate
source evidence. There is no remote collector or identity map to unwind.

## Consequences

The beta can be evaluated with honest local facts while preserving the existing
evidence owner, privacy boundary, and explicit authority for any later sharing.
