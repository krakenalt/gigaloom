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

Keep benchmark schemas stable and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness/test_diagnostics_tree.py tests/harness/test_doctor.py -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
