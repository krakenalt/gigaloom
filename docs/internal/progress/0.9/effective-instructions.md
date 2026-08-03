# GigaLoom 0.9 Effective Instructions progress

## RI-01 — Add bounded project instruction discovery

- Status: complete
- Baseline: `729bb65b429dad3a4438d8f6a9cc1d8a7366ca96`
- Scope: `src/gigaloom/projects/instruction_discovery.py`, project public facade,
  and focused discovery tests
- Contract/evidence: `gigaloom.instruction-discovery.v1`; Git-visible,
  content-free, project-root-bound discovery with symlink/path/count/size limits
- Tests: `UV_CACHE_DIR=.cache/uv uv run pytest
  tests/harness/test_project_instruction_discovery.py tests/harness/test_project.py
  tests/harness/test_project_impact.py
  tests/harness/architecture/test_package_boundaries.py -q -n 0` — 28 passed;
  focused Ruff check/format, `./scripts/ci-base.sh type-check`, and
  `git diff --check` passed
- Known limitations: provider selectors identify project-local files only and
  do not claim native-home visibility or prompt materialization
- Shared-file patch request: none

## RI-02 — Compute precedence, conflicts and omissions

- Status: complete
- Baseline: `7c480041a88b064433ad1452beb94a30e19dd438`
- Scope: Effective Instructions projection through the existing Context Lens,
  project public facade, and focused projection tests
- Contract/evidence: `gigaloom.effective-instructions.v1`; content-free source
  freshness, owner revision, precedence, token estimate, explicit omissions,
  uncertainty, and overlap review facts
- Tests: `UV_CACHE_DIR=.cache/uv uv run pytest
  tests/harness/test_effective_instructions.py
  tests/harness/test_project_instruction_discovery.py
  tests/harness/test_context_lens.py tests/harness/test_context_manifest.py
  tests/harness/test_project.py -q -n 0` — 34 passed; focused Ruff
  check/format, `./scripts/ci-base.sh type-check`, and `git diff --check` passed
- Known limitations: semantic content conflicts are intentionally not inferred;
  unknown adapter precedence remains explicit uncertainty
- Shared-file patch request: none

## RI-03 — Extend Context Lens read-only API contract

- Status: complete
- Baseline: `00275a3f6496b3d95f58d237c4140cfa5d20f380`
- Scope: existing Context Lens/Impact application service and route family,
  bounded summary/detail projections, route-local API tests
- Contract/evidence: additive `gigaloom.effective-instructions.v1` summary and
  exact discovery-digest-bound detail; max 100 sources per page, max 32 revision
  bindings, max 512 conflict facts
- Tests: `GIGALOOM_VENV=/private/tmp/gigaloom-09-foundation-019fc962/.venv
  PYTHONPATH=/private/tmp/giga-09-rules/src ./scripts/ci-base.sh pytest
  tests/harness/test_context_impact_api.py
  tests/harness/test_effective_instructions.py
  tests/harness/test_project_instruction_discovery.py
  tests/harness/test_context_lens.py
  tests/harness/architecture/test_0_9_boundaries.py
  tests/harness/architecture/test_package_boundaries.py
  tests/harness/architecture/test_module_budgets.py -q` — 43 passed with
  repository xdist defaults; focused Ruff check/format,
  `./scripts/ci-base.sh type-check`, and `git diff --check` passed
- Known limitations: API exposes content-free project-local impact only and
  never materializes, injects, or reads provider-native homes
- Shared-file patch request: after integrating A4, update the global route-count
  assertion in `tests/harness/test_async_execution_contracts.py` from the
  integrated route inventory (A4 alone changes 286 to 288), then regenerate
  `src/gigaloom/evidence/product_inventory/v1/inventory.json` with
  `giga harness capabilities --inventory --output <that-path>` so it includes
  `GET /api/project/effective-instructions` and
  `GET /api/project/effective-instructions/{source_id}`. Rebuild ignored Web
  assets at the integrated HEAD before package/full-suite gates.

### Final lane-gate notes

- Full `tests/harness` reached 2668 passed and 4 skipped before integration and
  environment gates. The only A4-caused failures were the two exclusive shared
  projections listed above and the `projects/api.py` hard budget; the latter is
  fixed by the compact `projects.api.instructions_api` facade (599/600 lines).
- The exact 5k-file timing node passed serially. Worker wake sockets and private
  tmux remained unavailable both under xdist and `-n 0`; all-extras sync and
  build-isolation tests were blocked by sandbox DNS for missing cached wheels.
