# AGENTS.md — Harness package

## Scope and boundary

These rules apply to `src/gigaloom/**` in addition to the root contract.

- Keep Harness-owned imports under `gigaloom.*`. Do not restore the
  old `gpt2giga.harness.*` namespace or add a broad compatibility shim.
- Keep the plugin entry-point group `gigaloom.harnesses.v1`; entry-point targets
  belong in `gigaloom.*`.
- Keep the gateway dependency exact unless an explicit release change includes
  installed-artifact compatibility evidence.
- Reviewed gateway boundaries are normalized protocol models used by Direct
  Chat and `from gpt2giga import run` in optional sidecar startup. Do not add a
  new gateway import without proving the standalone package contract.

## Runtime invariants

- Use only canonical user state under `~/.gigaloom` (or
  `GIGALOOM_DATA_DIR`) and project `.giga/`. The separate one-way migrator
  preserves a verified legacy backup; normal runtime must not dual-read or
  dual-write the old root.
- Redact before persistence and before API/UI serialization. Secret resolution
  may cross an explicit execution boundary but secret values must not appear in
  previews, records, logs, traces, diffs, or errors.
- Mutating actions must remain approval-gated where policy requires it.
  Worktree-mutating edit/apply flows must fail closed when isolation, approval,
  lease, or policy checks fail.
- Preserve idempotency, atomic writes, file locks, lease/cancellation semantics,
  and crash reconciliation when changing durable runtime flows.
- External commands must use explicit argv, controlled cwd/env, bounded output,
  and redacted records. Do not introduce `shell=True` or mutate a user's
  native Codex, Claude, or Gemini home.
- Keep Cockpit assets integrity-checked and available from installed artifacts.
  Do not add a second packaged UI or a new frontend build architecture without
  an explicit roadmap slice.

## Ownership guide

| Path under `src/gigaloom/` | Responsibility |
|---|---|
| `core/`, `contracts/` | Dependency-light primitives and public contracts |
| `sessions/`, `runtime/`, `execution/` | Durable state, jobs, policy, leases, and execution orchestration |
| `providers/`, `harnesses/`, `native/` | Provider protocols, built-in adapters, and native connectors |
| `integrations/`, `tools/`, `skills/` | Extension packages, tool policy, and portable skills |
| `projects/`, `attachments/` | Project state, bounded filesystems, worktrees, and attachments |
| `automation/`, `review/` | Workflows, schedules, evaluations, evidence, and review flows |
| `diagnostics/` | Doctor, compatibility, inventory, and benchmark implementation |
| `tui/`, `ui/` | Textual and FastAPI/Cockpit application surfaces |

Keep `ui/app.py` as composition; add cohesive API families to `ui/routers/`
instead of expanding the composition module. Use temporary data dirs and repos
in tests; never exercise real user state.

Root modules listed in `architecture/module-budgets.json` are either external
compatibility facades or ADR-recorded target-tree deviations. Do not add new
callers to them. Cross-context imports use the owning context's `api.py` or
documented public package facade; internal modules of another context are not a
supported boundary.

## Validation

Run from the repository root:

```bash
npm --prefix web ci --ignore-scripts
npm --prefix web run build
./scripts/ci-base.sh sync
./scripts/ci-base.sh ruff-check src/gigaloom tests/harness
./scripts/ci-base.sh ruff-format-check src/gigaloom tests/harness
./scripts/ci-base.sh pytest tests/harness -q
uv build --no-sources
```

`uv run giga doctor` is an environment smoke check, not a hermetic quality
gate. For UI changes, also verify the packaged asset test and perform browser QA
at relevant desktop and mobile widths. For metadata, imports, package data, or
release changes, run the root coverage gate plus the standalone base-artifact
and locked public-gateway tests.
