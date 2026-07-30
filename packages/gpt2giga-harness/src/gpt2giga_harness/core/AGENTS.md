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

New Python modules stay below 600 lines and avoid import-time work.

# Focused validation commands

`uv run pytest tests/harness/architecture -n 0`

# Owner thread/CODEOWNERS

T15/T20; `@krakenalt`.
