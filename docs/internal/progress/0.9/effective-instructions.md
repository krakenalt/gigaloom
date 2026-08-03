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
