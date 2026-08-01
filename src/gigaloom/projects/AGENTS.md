# Scope

Projects, environments, worktrees, governed Git actions, and backups.

# Public API

Cross-context callers use `projects/api.py`.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No UI implementation or concrete session/runtime repository imports.

# Persistence/security invariants

Bound paths to project roots and fail closed on approval or isolation failures.

# Performance budgets

Bound filesystem/Git scans and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'project or environment or worktree' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
