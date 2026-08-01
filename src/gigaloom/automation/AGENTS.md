# Scope

Workflows, schedules, evaluations, agents, arena, and authoring.

# Public API

Use package exports; add a narrow API before exposing internals cross-context.

# Allowed imports

Public execution, runtime, and sessions APIs plus contracts/core.

# Forbidden imports

No application-surface or concrete repository deep imports.

# Persistence/security invariants

Preserve approval, provenance, retry, and redaction guarantees.

# Performance budgets

Keep planning/evaluation bounded and honor `architecture/module-budgets.json`.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'workflow or schedule or eval or arena' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
