# Scope

Admission, preparation, invocation, persistence, and finalization orchestration.

# Public API

Use package exports; keep application-facing operations explicit.

# Allowed imports

Public APIs of sessions, runtime, harnesses, projects, and attachments.

# Forbidden imports

No deep imports into another bounded context or application surface.

# Persistence/security invariants

Fail closed on policy, approval, isolation, cancellation, and redaction.

# Performance budgets

Offload blocking work and ratchet the legacy package facade.

# Focused validation commands

`./scripts/ci-base.sh pytest tests/harness -k 'execution or session_runner' -q`

# Owner/CODEOWNERS

Owner: `@krakenalt`.
