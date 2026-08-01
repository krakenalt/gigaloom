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

Bound replay/evidence reads and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'evidence or review or provenance or replay' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
