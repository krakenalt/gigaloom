# Scope

Tool/MCP contracts, managed configuration, inventory, policy, and probes.

# Public API

Cross-context callers use package exports and `tools/mcp/api.py`.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No CLI, UI, or concrete session/runtime repository imports.

# Persistence/security invariants

Keep secret values out of previews, records, logs, and generated config.

# Performance budgets

Bound probes/inventories and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'tool or mcp' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
