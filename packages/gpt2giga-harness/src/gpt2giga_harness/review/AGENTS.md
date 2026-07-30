# Scope

Evidence, promotions, provenance, handoffs, replay, and support artifacts.

# Public API

Use package exports; add a narrow API before exposing internals cross-context.

# Allowed imports

Public sessions, runtime, and projects APIs plus contracts/core.

# Forbidden imports

No application-surface or concrete repository deep imports.

# Persistence/security invariants

Redact before storage/export and preserve reviewed-evidence provenance.

# Performance budgets

Bound replay and evidence reads; new modules stay below 600 lines.

# Focused validation commands

`uv run pytest tests/harness -k 'evidence or review or provenance or replay' -n 0`

# Owner thread/CODEOWNERS

T18/T20; `@krakenalt`.
