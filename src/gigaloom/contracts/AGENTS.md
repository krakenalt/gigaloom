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

No import-time discovery; honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness/architecture/test_shared_contracts.py tests/harness/architecture/test_package_boundaries.py -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
