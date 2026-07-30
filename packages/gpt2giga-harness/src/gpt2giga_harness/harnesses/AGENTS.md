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

Keep adapter imports lazy and new modules below 600 lines.

# Focused validation commands

`uv run pytest tests/harness -k 'harness or adapter' -n 0`

# Owner thread/CODEOWNERS

T17/T20; `@krakenalt`.
