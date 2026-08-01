# Scope

FastAPI composition, routers, streaming, application services, and Cockpit assets.

# Public API

Keep `app.py` as composition and route families in cohesive routers.

# Allowed imports

Public application and bounded-context APIs.

# Forbidden imports

No direct repository access from routers when an application/query API exists.

# Persistence/security invariants

Preserve CSRF/auth, approval, redaction, SSE resnapshot, and asset integrity.

# Performance budgets

Bound polling/streaming and ratchet every listed legacy module.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'ui or router or sse' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
