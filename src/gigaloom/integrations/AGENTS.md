# Scope

Integration catalog, packages, lifecycle, runtime, federation, and SDK services.

# Public API

Cross-context callers use `integrations/api.py`.

# Allowed imports

`contracts`, `core`, and public tools/skills APIs.

# Forbidden imports

No UI implementation or deep imports into other contexts.

# Persistence/security invariants

Preserve transactional compensation, trust policy, and secret redaction.

# Performance budgets

Keep discovery bounded and new modules below 600 lines.

# Focused validation commands

`uv run pytest tests/harness -k integration -n 0`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
