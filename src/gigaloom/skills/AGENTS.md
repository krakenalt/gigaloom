# Scope

Portable skills, built-in skills, validation, transport, and catalog clients.

# Public API

Cross-context callers use `skills/api.py`.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No application-surface or concrete durable-store imports.

# Persistence/security invariants

Validate paths and package contents before installation or publication.

# Performance budgets

Keep catalog operations bounded and new modules below 600 lines.

# Focused validation commands

`uv run pytest tests/harness -k skill -n 0`

# Owner thread/CODEOWNERS

T16/T20; `@krakenalt`.
