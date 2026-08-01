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

Keep file reads bounded and new modules below 600 lines.

# Focused validation commands

`uv run pytest tests/harness -k attachment -n 0`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
