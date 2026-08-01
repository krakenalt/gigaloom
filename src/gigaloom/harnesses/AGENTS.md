# Scope

Harness contracts, built-in adapters, and provider-neutral execution events.

# Public API

Cross-context callers use `harnesses/api.py`.

# Allowed imports

Public provider and native APIs plus `contracts` and `core`.

# Forbidden imports

No UI implementation or concrete session/runtime repository imports.

# Persistence/security invariants

Use controlled argv/cwd/env, bounded output, and redacted diagnostics.

# Performance budgets

Keep adapter imports lazy and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'harness or adapter' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
