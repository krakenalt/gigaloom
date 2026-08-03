# GigaLoom 0.9 Thread Relay progress

## TR-01 — Define locator, read projection, envelope and receipt

- Status: complete
- Baseline: `729bb65b429dad3a4438d8f6a9cc1d8a7366ca96`
- Scope: `src/gigaloom/sessions/thread_relay/`, public session facade exports,
  and `tests/harness/thread_relay/test_contracts.py`
- Contract/evidence: `ThreadLocatorV1`, `ThreadReadProjectionV1`,
  `ThreadMessageEnvelopeV1`, `ThreadDeliveryReceiptV1`; canonical strict codecs
  and SHA-256 digests; bounded visible content/cursors; user-only delivery with
  actor/project/revision/TTL/idempotency/depth enforcement; digest-only receipts
- Tests: `pytest tests/harness/thread_relay/test_contracts.py
  tests/harness/architecture/test_0_9_boundaries.py -q -n 0` — 15 passed;
  `pytest tests/harness/architecture/test_shared_contracts.py
  tests/harness/architecture/test_package_boundaries.py
  tests/harness/thread_relay/test_contracts.py -q -n 0` — 15 passed; focused
  Ruff check and format — passed; focused `ty check` — passed;
  `git diff --check` — passed
- Known limitations: persistence, recovery, structured-session delivery, Codex
  and ACP adapters, and route-local CLI/API handlers remain in TR-02 through
  TR-05; expiry/outstanding-child/revision state is enforced by the future
  delivery service against these frozen contracts
- Shared-file patch request: none

## TR-02 — Persist delivery through existing owners

- Status: complete
- Baseline: `16338d6210cc6a7f457b3a76da3b52e6b24b27f3`
- Scope: `src/gigaloom/sessions/thread_relay/repository.py`, session public
  facade exports, and `tests/harness/thread_relay/test_repository.py`
- Contract/evidence: the repository lives under the existing `sessions/`
  owner, stores immutable envelopes and append-only digest-only receipts in a
  dedicated SQLite action ledger, hashes idempotency keys, enforces optimistic
  target revision and the four-outstanding-child bound, exposes bounded
  incoming/outgoing cursor pages, expires TTLs, and fails accepted ambiguous
  deliveries closed during explicit restart recovery; it stores no transcript
- Tests: `pytest tests/harness/thread_relay tests/harness/test_session_store.py
  tests/harness/test_session_migrations.py
  tests/harness/architecture/test_0_9_boundaries.py
  tests/harness/architecture/test_package_boundaries.py -q -n 0` — 48 passed;
  focused Ruff check/format and `ty check` — passed; `git diff --check` — passed
- Known limitations: target session mutation and enqueue/steer semantics remain
  in TR-03; provider capabilities/adapters and route-local CLI/API handlers
  remain in TR-04 and TR-05
- Shared-file patch request: none

## TR-03 — Implement GigaLoom structured-session delivery

- Status: complete
- Baseline: `d07036370f4178933346edfa67a2834b96b11afb`
- Scope: `src/gigaloom/execution/thread_relay/`, scoped expiry support in
  `src/gigaloom/sessions/thread_relay/repository.py`, the public session model
  facade, and `tests/harness/thread_relay/test_structured_sessions.py`
- Contract/evidence: bounded actor/project-owned list/read uses the derived
  session index and recent-message/run pages; visible messages are redacted and
  exclude hidden/system state; follow-up/message delivery resolves one approved
  message reference, rechecks the target revision, and submits through the
  existing application turn/job owner with content-free provenance; steering
  requires the exact proven active turn id; pending cancellation and scoped TTL
  expiry append immutable receipts; failed/ambiguous mutations fail closed
- Tests: `pytest tests/harness/thread_relay
  tests/harness/architecture/test_0_9_boundaries.py
  tests/harness/architecture/test_package_boundaries.py
  tests/harness/architecture/test_module_budgets.py
  tests/harness/test_session_application.py -q -n 0` — 36 passed; focused Ruff
  check/format and `ty check` — passed; `git diff --check` — passed
- Known limitations: only GigaLoom-owned structured sessions are implemented;
  Codex and ACP capability adapters remain in TR-04, and route-local CLI/API
  modules remain in TR-05; application/root composition is intentionally left
  to the integrator
- Shared-file patch request: none

## TR-04 — Add Codex and ACP capability adapters

- Status: complete
- Baseline: `c722ae638fe42b4b1917151d9064171d38dcb735`
- Scope: provider-neutral capability results plus Codex app-server v2 and ACP
  adapters under `src/gigaloom/execution/thread_relay/`, lazy ACP exports in the
  existing harness facade, required session-bound constants, and
  `tests/harness/thread_relay/test_provider_adapters.py`
- Contract/evidence: Codex list/read/start/steer uses only the pinned public
  app-server methods and revalidates target revision and exact active turn;
  visible Codex user/assistant content is bounded and redacted while reasoning
  and tool payloads are excluded; ACP list/load/prompt is enabled only from the
  immutable initialize capability snapshot; unadvertised ACP operations return
  content-free capability facts and ACP steer remains unsupported rather than
  being emulated; neither adapter reads private JSONL or native home state
- Tests: `pytest tests/harness/thread_relay
  tests/harness/architecture/test_0_9_boundaries.py
  tests/harness/architecture/test_package_boundaries.py
  tests/harness/architecture/test_module_budgets.py -q -n 0` — 39 passed;
  existing Codex exact-steer and ACP session lifecycle nodes — 2 passed;
  focused Ruff check/format and `ty check` — passed
- Known limitations: ACP does not expose transcript reads or active-turn steer,
  so those absences remain explicit capability/projection facts; route-local
  CLI/API modules and composition patch requests remain in TR-05
- Shared-file patch request: none

## TR-05 — Add route-local CLI/API modules

- Status: complete
- Baseline: `1b60c71978015a339ef1064c6028951cd4acdea4`
- Scope: shared route action port and digest-only preview validator,
  `src/gigaloom/cli_commands/{commands,handlers}/thread_relay.py`,
  `src/gigaloom/ui/{routers,schemas}/thread_relay.py`, and
  `tests/harness/thread_relay/test_routes.py`; no shared root registration
- Contract/evidence: bounded list/read/send/status surfaces cover GigaLoom,
  Codex, and ACP source selectors; both CLI and HTTP send paths obtain and
  validate a content-free preview digest immediately before mutation;
  `session send --dry-run --json` returns the preview without invoking send;
  preview output is rejected if it echoes text/content/prompt/message fields;
  HTTP inputs enforce source, identity, text, attachment, and page bounds
- Tests: `pytest tests/harness/thread_relay
  tests/harness/architecture/test_0_9_boundaries.py
  tests/harness/architecture/test_package_boundaries.py
  tests/harness/architecture/test_module_budgets.py -q -n 0` — 43 passed;
  root-namespace and existing CLI registry regression suite — 17 passed;
  focused Ruff check/format and `ty check` — passed; `git diff --check` — passed
- Known limitations: shared composition remains intentionally absent until I1;
  the restricted agent tool surface and product UI are owned by Wave B
- Shared-file patch request: integrator should (1) call
  `cli_commands.commands.thread_relay.register(session_subparsers, common)`
  from the existing `session` parser composition, preserving the existing
  `session list` and using `session threads` for relay listing; (2) bind
  `_handle_thread_list`, `_handle_thread_read`, `_handle_thread_send`, and
  `_handle_thread_status` in `cli_commands/registry.py` to one scoped
  `ThreadRelayCommandHandlers`; (3) add one actor/project-bound
  `ThreadRelayRouteActions` implementation to `AppServices`; and (4) include
  `ui.routers.thread_relay.create_router(services.thread_relay_actions)` in
  `ui/router_registry.py` before the shell catch-all
