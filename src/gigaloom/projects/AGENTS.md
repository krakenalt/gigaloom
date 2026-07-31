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

Bound filesystem/Git scans and keep new modules below 600 lines.

# Focused validation commands

`uv run pytest tests/harness -k 'project or environment or worktree' -n 0`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
