# Scope

Native client discovery, capture, process control, and session connectors.

# Public API

Use package exports; expose new cross-context behavior through a narrow facade.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No application surface or concrete durable-store imports.

# Persistence/security invariants

Never mutate real native homes in tests; bound and redact subprocess output.

# Performance budgets

Avoid eager discovery and ratchet every listed legacy module.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k native -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
