# Scope

Authoritative session state, indexes, migrations, and query projections.

# Public API

Cross-context callers use `sessions/api.py`.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No runtime worker, CLI, or UI implementation imports.

# Persistence/security invariants

Preserve atomic writes, migration recovery, redaction, and rebuildability.

# Performance budgets

Bound scans and page queries; ratchet every listed legacy module.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'session or catalog' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
