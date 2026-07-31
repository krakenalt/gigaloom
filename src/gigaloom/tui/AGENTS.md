# Scope

Textual composition, clients, projections, controllers, widgets, and resources.

# Public API

Treat TUI modules as an application surface, not a domain API.

# Allowed imports

Public application and bounded-context APIs.

# Forbidden imports

No direct filesystem/SQLite repository access when an application API exists.

# Persistence/security invariants

Preserve cancellation, approval, redaction, and native-home isolation.

# Performance budgets

Avoid blocking the event loop and ratchet every listed legacy module.

# Focused validation commands

`uv run pytest tests/harness -k tui -n 0`

# Owner thread/CODEOWNERS

T09/T10/T20; `@krakenalt`.
