# Scope

Provider profiles, authentication, compatibility, and transport contracts.

# Public API

Cross-context callers use `providers/api.py`.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No CLI, UI, or concrete session/runtime storage imports.

# Persistence/security invariants

Never expose credentials; preserve public protocol shapes and TLS choices.

# Performance budgets

Avoid import-time network/discovery work; ratchet listed target modules.

# Focused validation commands

`uv run pytest tests/harness -k 'provider or compatible' -n 0`

# Owner thread/CODEOWNERS

T17/T20; `@krakenalt`.
