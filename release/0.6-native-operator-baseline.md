# GigaLoom 0.6 Native Operator Baseline

This report freezes the release baseline required by roadmap slice `P0-01`.
It records repository state at `G0`; it does not authorize a release, tag,
publication, or migration.

## Repository baseline

| Field | Value |
| --- | --- |
| Recorded | 2026-07-30 |
| Repository | `https://github.com/krakenalt/gigaloom.git` |
| Branch used for the baseline | `release/v0.6.0a1` |
| `G0` | `6611277833ffc7aab122aefa2fa8aecafb76c847` |
| Distribution | `gigaloom` |
| Version at `G0` | `0.5.1a2` |
| Supported Python range | `>=3.11,<3.15` |
| Locked packages | 107 |
| Tracked files | 1,371 |
| Tracked Python files | 991 |
| Tracked Python test files | 201 |
| Frontend test/spec files | 43 |

The tracked worktree was clean before the baseline was measured.

## Toolchain

| Tool | Baseline version |
| --- | --- |
| macOS | Darwin 25.5.0, arm64 |
| Git | 2.50.1 (Apple Git-155) |
| uv | 0.10.9 |
| Project Python | 3.13.8 |
| System Python | 3.14.6 |
| Ruff | 0.15.22 |
| pytest | 9.1.1 |
| Node.js | 22.13.0 |
| npm | 11.17.0 |
| tmux | 3.7b |
| Codex CLI | 0.144.5 |

## Locked dependency roots

The registry-resolved `uv.lock` resolves 107 packages. Direct runtime roots at
`G0` resolve to:

| Dependency | Locked version |
| --- | --- |
| anyio | 4.14.2 |
| FastAPI | 0.140.13 |
| Pydantic | 2.13.4 |
| PyJWT | 2.13.0 |
| python-dateutil | 2.9.0.post0 |
| PyYAML | 6.0.3 |
| Starlette | 1.3.1 |
| Textual | 8.2.8 |
| Uvicorn | 0.52.0 |

Optional compatibility roots resolve to `gpt2giga==0.2.6a1`,
`gigachat==0.2.3a1`, and `claude-agent-sdk==0.2.122`. Direct development roots
resolve to Black 26.5.1, httpx2 2.9.1, pre-commit 4.6.1, pytest 9.1.1,
pytest-asyncio 1.4.0, pytest-cov 7.1.0, pytest-mock 3.15.1,
pytest-xdist 3.8.0, and Ruff 0.15.22.

The frontend lock resolves the direct product roots React 19.2.7,
React DOM 19.2.7, React Query 5.101.2, React Router 1.170.17,
react-markdown 10.1.0, and KaTeX 0.16.22. Its direct build and test roots are
TypeScript 6.0.2, Vite 8.1.3, Vitest 4.1.10, ESLint 9.39.5, and Playwright
1.61.0.

## Test baseline

The base, locked environment collected 2,094 non-live test items. The exact
non-live quality run completed with:

- 2,091 passed;
- 5 skipped;
- 84 warnings;
- 85.91% statement coverage, above the 80% gate.

`tests/live/` is excluded before collection. Its vLLM smoke imports the optional
gateway distribution at module import time, so marker-only deselection is not a
valid base-install exclusion.

The frontend gate completed with 42 Vitest files and 156 tests passing, plus
TypeScript, ESLint, and deterministic asset checks. The generated Cockpit asset
tree contains 54 files and has output digest
`38ad5f91628a68b11353b55fe0e565e5879116a50dfbf81affc761aeaef2797e`.

## Legacy identifier census

Counts are exact, case-sensitive occurrences in tracked text files at `G0`.
They intentionally include source, tests, documentation, and historical
compatibility material; they are a migration baseline, not a removal verdict.

| Identifier | Occurrences |
| --- | ---: |
| `gpt2giga_harness` | 3,866 |
| `packages/gpt2giga-harness` | 176 |
| `cockpit_v2` | 69 |
| `cockpit-v2` | 203 |
| `@gpt2giga/harness-cockpit-v2` | 4 |
| `gpt2giga-cockpit` | 10 |
| `GIGALOOM_COCKPIT_OUTPUT` | 3 |
| `agent_workbench.` | 55 |
| `gpt2giga-harness-v` | 34 |
| `gigaloom-v` | 25 |

## Current state roots

This is a source-defined inventory. The baseline did not inspect or mutate
actual user state.

- The default durable data root is `~/.gpt2giga/harness`.
- `GPT2GIGA_HARNESS_DATA_DIR` overrides that durable data root.
- Managed Git worktrees live under the data root's `worktrees/` subdirectory.
- Project-local configuration is `.giga/harness.toml`.
- Project-local authored state uses `.giga/prompts/`, `.giga/evals/`,
  `.giga/agents/`, `.giga/workflows/`, and `.giga/schedules/`.

The canonical 0.6 target root and migration behavior remain decisions for the
following ADR and migration slices.

## Validation evidence

The baseline was produced and checked with:

```text
npm --prefix packages/gpt2giga-harness/frontend run build
./scripts/ci-base.sh sync
./scripts/ci-base.sh ruff-check
./scripts/ci-base.sh ruff-format-check
npm --prefix packages/gpt2giga-harness/frontend run check
GPT2GIGA_HARNESS_DATA_DIR=<isolated-temp-root> \
  ./scripts/ci-base.sh pytest tests/ --ignore=tests/live \
  --cov=. --cov-report=term --cov-fail-under=80
```

Ruff reported no violations, Ruff formatting reported all 991 Python files
already formatted, and all gates above passed. Tests used a private temporary
data root so the real native Harness and project state remained untouched.
