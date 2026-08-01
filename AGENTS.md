# AGENTS.md — OpenAI Codex repository contract

## Authority and precedence

- This file applies repository-wide. The nearest nested `AGENTS.md` adds
  context-specific constraints and wins on conflicts.
- The user's request defines the authorized outcome. A review or diagnosis is
  read-only unless the user also asks for changes. A change request does not
  authorize commits, pushes, tags, releases, provider mutations, or live-service
  calls unless those actions are explicit or required by an active roadmap.
- Work from the repository root unless a command explicitly changes directory.
- Prefer source, tests, manifests, and current Git state over remembered paths or
  stale prose. Use `git ls-files` when mapping shipped code.

## Operating loop

1. Run `git status --short`; preserve unrelated and untracked user work.
2. Read the exact roadmap, issue, test, config, or artifact named by the user.
3. Locate the owning code, its nearest `AGENTS.md`, and focused tests.
4. Make the smallest coherent vertical change that satisfies the request.
5. Validate the changed layer first, then broaden checks in proportion to risk.
6. Report changed behavior, exact checks, measured evidence, and open gates.

Do not clean adjacent code, regenerate unrelated artifacts, or rewrite user work
merely because it is present. If the requested target is ambiguous and the
choice would materially change the result, stop and ask.

## Route by surface

| Surface | Owner and source of truth | First validation |
| --- | --- | --- |
| Python product | `src/gigaloom/` and nearest package contract | Focused pytest node plus Ruff |
| Web UI | `web/` | Relevant frontend test/build |
| Release/version | `release/version.toml`, projected by `scripts/release.py` | `./scripts/release verify` and release tests |
| Documentation | `docs/`, `docs-site/`, README/locales/sidebar | Link checks and Docusaurus build |
| Performance | `benchmarks/gigaloom_performance/` and focused counters | Identical before/after benchmark |
| Packaging/deploy | `pyproject.toml`, lockfile, Docker/deploy/Traefik files | Wheel, sdist, npm pack, and aligned smoke checks |

## Product and architecture invariants

- This is a standalone `uv` project. The root `pyproject.toml` owns package
  metadata, dependencies, entry points, and supported Python versions;
  `release/version.toml` is the canonical product-version source.
- `src/gigaloom/` owns the Python namespace. The independently built frontend
  lives under `web/` and its ignored asset tree must exist before a clean sync.
- The base distribution must install, test, and build without a gateway source
  checkout. Optional gateway compatibility uses only the exact public dependency
  in the committed registry-resolved `uv.lock`; never add sibling/editable/local,
  branch, candidate-URL, submodule, or temporary-index overrides.
- Use absolute imports, Ruff formatting, and concise Google-style docstrings.
  Keep package imports inert: no network, subprocess, filesystem discovery, or
  large dependency graph solely to answer metadata commands.
- OpenAI-, Anthropic-, Gemini-, and GigaChat-shaped responses, SSE events, route
  aliases, defaults, and accepted parameters are public compatibility contracts.
  Changes require focused compatibility tests and documentation.
- Preserve redaction at storage, observability, diagnostics, admin preview, and
  Harness UI boundaries. Do not blanket-redact public compatibility responses;
  content capture remains opt-in.
- Never commit credentials, tokens, real `.env` values, local certificates, raw
  captured traffic, or secret-bearing fixtures.
- Keep README/sidebar/locales aligned when documentation changes. Deployment
  edits must keep `deploy/`, `Dockerfile*`, `traefik/`, safe env examples, user
  docs, image contents, health checks, and published links consistent.

## Simplicity and performance

- Prefer deletion, direct composition, and an existing public API over a new
  wrapper, registry, factory, service layer, or compatibility alias.
- Add an abstraction only when it enforces a required boundary or removes
  concrete duplication/invalid states for present callers. Do not build for
  hypothetical variants.
- Keep one owner for each constant, policy, serialization shape, and version.
  Generated projections must identify their canonical input and verification.
- Avoid import-time work, unbounded scans, repeated serialization, subprocesses
  in loops, and lock-held I/O. Preserve idempotency and transaction boundaries
  when optimizing durable paths.
- A performance claim requires comparable before/after runs: same workload,
  dependency lock, machine, warmups, and sample count. Report p50 and p95 plus
  `(before - after) / before * 100`; add a deterministic counter or structural
  test when wall-clock timing alone cannot prove the changed mechanism.

## Local, ignored, and user state

- `local/` may contain secrets, wheels, media, notebooks, and experiments. Do
  not inspect or edit it unless the user explicitly puts it in scope.
- `docs/internal/`, `scripts/internal/`, and sometimes `docs/codex/` are ignored
  coordination state. Use `find` and `git check-ignore -v` when named; never
  force-add them without explicit publication scope.
- Runtime state under `~/.gigaloom/`, project `.giga/`, and native Codex, Claude,
  or Gemini homes belongs to the user. `~/.gpt2giga/harness` is a legacy
  migration source, not the current write target. Tests must use temporary homes.

When continuing a named local roadmap:

1. Read its exact roadmap and progress file.
2. Reconcile progress against branch, worktree, HEAD, and actual ancestry.
3. Resume only the next unfinished vertical slice.
4. Commit that slice only when the roadmap requires it.
5. Record the full commit hash in ignored progress state without staging it.

## Validation ladder

Build frontend assets before the first clean-checkout sync:

```bash
npm --prefix web ci --ignore-scripts
npm --prefix web run build
./scripts/ci-base.sh sync
./scripts/ci-base.sh sync-all-extras
./scripts/ci-public-gateway.sh
```

Repository quality gate:

```bash
./scripts/ci-base.sh ruff-check
./scripts/ci-base.sh ruff-format-check
./scripts/ci-base.sh type-check
./scripts/ci-base.sh pytest tests/ --cov=. --cov-report=term --cov-fail-under=80
```

Packaging and documentation gates:

```bash
uv build --wheel --sdist --no-sources
npm --prefix web run build:npm
npm --prefix web run pack:verify
npm --prefix docs-site run build
```

Use exact focused pytest node IDs while iterating. Reserve `-n 0` for a single
node whose worker startup dominates; directory, multi-file, and full-suite runs
use repository xdist defaults. Run the full quality gate for cross-package,
public-compatibility, security, release, packaging, or broad refactor changes.
Build wheel, sdist, and the npm package after metadata, dependency, entry-point,
package-data, Docker, or release changes. Never run `tests/live/` or real-service
examples unless explicitly requested and safely configured.

## Git, release, and completion

- Use Conventional Commit prefixes such as `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, and `ci:`.
- Commit only when the user asks or an active roadmap explicitly requires it.
  Stage only task files, inspect `git diff --cached`, and recheck status.
- `./scripts/release bump <version>` projects and verifies version files; it
  does not authorize changelog invention, commits, tags, publishing, signing,
  registry/provider changes, or other external side effects.
- Report exact checks, skipped environment-dependent gates, measured performance
  evidence, and the final commit hash when a commit was authorized.
