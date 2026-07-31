# Scope

Durable jobs, leases, attempts, approvals, events, and runtime repositories.

# Public API

Cross-context callers use `runtime/api.py`.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No CLI, UI, or provider-specific application imports.

# Persistence/security invariants

Preserve transactions, idempotency, cancellation, lease recovery, and redaction.

# Performance budgets

Use bounded claims/maintenance and ratchet every listed legacy module.

# Focused validation commands

`uv run pytest tests/harness -k 'runtime or worker or lease' -n 0`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
