# Scope

Attachment models, persistence, validation, and bounded file handling.

# Public API

Use package exports; add an `api.py` before exposing cross-context internals.

# Allowed imports

`contracts` and `core`.

# Forbidden imports

No CLI, FastAPI, or concrete runtime repository imports.

# Persistence/security invariants

Validate paths, content limits, and redaction before persistence.

# Performance budgets

Keep file reads bounded and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k attachment -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
