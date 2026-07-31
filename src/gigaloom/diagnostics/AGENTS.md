# Scope

Doctor, compatibility, product inventory, and performance diagnostics.

# Public API

Use explicit diagnostics subpackage APIs; root modules are compatibility only.

# Allowed imports

Public application and bounded-context APIs.

# Forbidden imports

No direct persistence mutation or application-surface implementation imports.

# Persistence/security invariants

Diagnostics are read-only by default and redact exported support data.

# Performance budgets

Benchmark schemas stay stable; new implementation modules stay below 600 lines.

# Focused validation commands

`uv run pytest tests/harness/test_diagnostics_tree.py tests/harness/test_doctor.py -n 0`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
