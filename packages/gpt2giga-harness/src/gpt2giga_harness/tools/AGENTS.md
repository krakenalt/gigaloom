# Scope

Tool/MCP contracts, managed configuration, inventory, policy, and probes.

# Public API

Cross-context callers use `tools/api.py` and declared subpackage APIs.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No CLI, TUI, UI, or concrete session/runtime repository imports.

# Persistence/security invariants

Keep secret values out of previews, records, logs, and generated config.

# Performance budgets

Bound probes and inventories; new modules stay below 600 lines.

# Focused validation commands

`uv run pytest tests/harness -k 'tool or mcp' -n 0`

# Owner thread/CODEOWNERS

T16/T20; `@krakenalt`.
