# Product beta pilot contract

**Effective date:** 2026-08-04

**Scope:** GigaLoom 0.9 local design-partner evidence

## Opt-in and privacy boundary

Participation and every report export require an explicit user action. GigaLoom
does not schedule, upload, transmit, or share the report. The report contains
aggregate statuses, counts, durations, basis-point ratios, reason codes, and
source truncation facts. It excludes prompts, responses, message/event content,
credentials, account material, unrestricted repository paths, and user intent
inferred from filesystem timestamps.

The participant chooses the output file and controls any later sharing. Support
does not request native-agent homes, raw transcripts, captured traffic, or
credentials as a condition of participation.

## Cohort and metric definitions

The pilot cohort is the explicitly opted-in set of projects running the pinned
GigaLoom 0.9 candidate. Reports are scoped to one catalog project and at most
366 days. Activation begins at the retained catalog project creation time, not
installation time or a filesystem proxy. Success, approvals, recovery, review,
Work/Inbox/Automations/Library use, Thread Relay, and gateway metrics are
computed only from their existing durable owners. Missing or unavailable facts
remain `unknown`; bounded scans declare truncation.

## Stop criteria

Stop collection and do not request a report when any of these holds:

- the participant withdraws opt-in;
- content, credentials, private paths, or provider-hidden state appear;
- generation requires telemetry, a network exporter, private-home scraping, or
  executing a dependency;
- a metric cannot distinguish retained evidence from an inference;
- project/actor scope, redaction, or boundedness cannot be established;
- the candidate causes data loss, authority bypass, or silent route fallback.

## Decision contract

Reviewers may decide only `continue`, `narrow`, `internal_only`, `defer`, or
`stop`. A decision records the report schema/version, cohort boundary, unknown
and truncated metrics, and the reason. Evidence does not authorize outreach,
data access, publication, telemetry, provider traffic, or product rollout; each
requires separate explicit authorization.
