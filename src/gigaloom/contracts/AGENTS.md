# Scope

Stable, dependency-light public contracts and serialized shapes.

# Public API

Use explicit contract modules and preserve accepted wire shapes.

# Allowed imports

`core` plus standard library and third-party typing/model libraries.

# Forbidden imports

No runtime, storage, UI, CLI, or provider implementations.

# Persistence/security invariants

Preserve schema compatibility and redaction annotations.

# Performance budgets

No import-time discovery; new modules stay below 600 lines.

# Focused validation commands

`uv run pytest tests/harness/architecture tests/harness/test_contracts.py -n 0`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
