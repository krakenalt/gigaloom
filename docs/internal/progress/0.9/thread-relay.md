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
