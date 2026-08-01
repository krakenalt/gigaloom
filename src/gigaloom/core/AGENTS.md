# Scope

Dependency-light primitives shared across bounded contexts.

# Public API

Import explicit modules; keep `__init__.py` lightweight.

# Allowed imports

Standard library and third-party libraries only.

# Forbidden imports

No product context or application-surface imports.

# Persistence/security invariants

No user-state I/O or secret resolution.

# Performance budgets

Avoid import-time work and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness/architecture -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
