# Work, threads, and context

GigaLoom 0.9 organizes the primary journey as
`Project -> Thread -> Run -> Evidence -> Action`. The Work screen composes the
existing project, session, runtime, evidence, approval, and action owners; it
does not create a second transcript or execution store.

## Start in Work

```sh
giga ui
```

Open `http://127.0.0.1:8091/web/work`. Select a project and thread, then review
the agent, route/model support, workspace, Effective Instructions summary,
authority mode, and blockers before submitting. The resulting run is shown as
one causal narrative. Inbox contains items that need attention, Automations
contains schedules and workflows, and Library contains reusable definitions
and the bounded Thread Relay browser.

## Thread Relay

Thread Relay can list or read an admitted GigaLoom, Codex app-server, or ACP
thread and can deliver one user-role message to a supported target. Reads are
bounded and redacted. It does not copy a whole transcript, request hidden
reasoning, read provider-private homes, or create an autonomous relay loop.

List and inspect a local project thread:

```sh
giga session threads --json
giga session read THREAD_ID --json
```

Preview a delivery without persisting a receipt, starting a turn, or calling a
provider:

```sh
giga session send THREAD_ID --text "review failing tests" --dry-run --json
```

The short dry-run reads the current target revision and creates only
short-lived preview guards. A real delivery is deliberately more explicit:

```sh
giga session send THREAD_ID \
  --text "review failing tests" \
  --expected-revision REVISION \
  --idempotency-key UNIQUE_KEY \
  --expires-at RFC3339_TIMESTAMP \
  --json
```

Use `--source codex` or `--source acp` only when the corresponding pinned
adapter capability is available. Steering additionally requires the exact
active turn id. Actor/project binding, revision, TTL, idempotency, relay depth,
and attachment availability are revalidated before mutation. Receipts retain
content digests and bounded status facts, not message text.

## Editor schemas

Packaged JSON Schemas describe the `.giga` agent, workflow, schedule,
evaluation, and related editor artifacts. They help editors validate authored
files; the normal `.giga` parsers remain the runtime source of truth.

```sh
giga schema list --json
giga schema agent
```

Use `--output PATH` to write a selected schema. Exporting a schema does not
change project configuration or grant execution authority.

## Attachment encodings

Text attachments are decoded deterministically without replacement
characters. Supported evidence values are `utf-8`, `utf-8-sig`, UTF-16 LE/BE,
UTF-32 LE/BE, `windows-1251`, and `koi8-r`. The preview records charset,
confidence class, BOM presence, truncation, replacement count, failure reason,
and source digest without echoing source text.

Malformed BOM input, ambiguous legacy text, binary signatures, control-heavy
payloads, size bombs, and undecodable bytes are rejected or omitted with an
explicit reason. GigaLoom never silently substitutes `U+FFFD` or changes the
stored source bytes. Attachment records created before 0.9 remain readable;
their optional charset evidence may be absent.

## Effective Instructions

Before a run, Work reads the project-scoped
`GET /api/project/effective-instructions` projection. It shows discovered
sources, scopes, precedence reported by the owning adapter, included and
omitted counts, conflicts, uncertainty, freshness, bounds, and a digest.

This view is read-only. GigaLoom does not merge or inject `AGENTS.md`,
`GEMINI.md`, `.claude`, `.codex`, Cursor, or project rules, and it does not read
private provider homes. Unknown or stale capability facts remain visible and
fail closed when a decision depends on them.

## Local beta evidence

An opted-in participant can export one bounded, content-free report for a
catalog project:

```sh
giga evidence product-beta --project PROJECT_ID --output report.json
```

The command writes only the selected local file. It performs no upload,
telemetry, provider call, account lookup, or outreach. Prompts, responses,
message/event content, credentials, unrestricted paths, and attachment bodies
are excluded. See the [Product beta pilot program](pilot-program.md).

## Upgrade and rollback

The 0.9 projections and records are additive. Existing 0.8.1 sessions are not
rewritten into relay records; old attachments remain readable; new instruction
fields can be ignored by old Context Lens clients; and no gateway route becomes
the default automatically. Back up `~/.gigaloom` and project `.giga/` state
before upgrading.

To roll back, stop GigaLoom, disable 0.9 gateway profiles, stop only the managed
sidecar lease, and reinstall 0.8.1 with the verified pre-upgrade archive when a
state restore is required. Immutable launch/delivery receipts may remain as
unknown records for the older binary; native homes and existing session history
are not rewritten. Removing `gpt2giga 0.3` disables its routes instead of
remapping them to a legacy fallback. See [Operations](operations.md) for the
state backup and restore procedure.
