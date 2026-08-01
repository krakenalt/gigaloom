# ADR: Final target-tree deviations and compatibility window

## Status

Accepted as the final target-tree audit. This decision records the remaining
differences from the target tree; it does not make those paths preferred for new
code.

## Context

The bounded contexts under `gigaloom/` are now the only approved homes
for new production behavior. The repository still ships two different kinds of
root-level compatibility state:

1. import-only facades that preserve published Python module paths and object
   identity; and
2. pre-existing executable modules or resource contexts whose owning structural
   migration is not complete.

Conflating those categories made the old root allowlist ambiguous and left
completed package-migration gates attached to entries that were not actually
removable. The final audit checked production, tests, documentation, entry points, package
data, and installed-artifact compatibility coverage.

## Decision

`module-budgets.json` separates:

- `compatibility_*_groups`, which may contain only documented external import
  facades or published package-data paths; and
- `target_deviation_*_groups`, which may contain existing behavior only when
  this ADR, an owner, a reason, and a concrete removal gate are present.

New root modules and contexts remain rejected. New first-party code must import
bounded-context public facades rather than any root compatibility path.

No compatibility shim is approved for deletion at this gate. Every reviewed
shim is referenced by compatibility tests, documentation, package-isolation
smoke tests, or still lacks the required integrator decision. Removing one
would therefore violate the documented compatibility window. Deletion requires
all of:

1. no production, test, example, documentation, entry-point, serialized-name,
   or dynamic-import consumer;
2. clean wheel and sdist import/entry-point parity;
3. an explicit compatibility decision after the 1.0 import window; and
4. a dedicated removal commit.

## Recorded structural deviations

| Owner | Paths | Removal gate |
|---|---|---|
| sessions/architecture | root session export/title implementations | `sessions-api-migration` |
| runtime/architecture | over-budget runtime repository and worker modules | context-specific hard-limit fission |
| execution/architecture | `application/`, execution/workbench root implementations, and `execution/__init__.py` | `execution-application-facade-completion` and hard-limit fission |
| ui/architecture | workbench protocol/resources and over-budget UI modules | `workbench-application-api-migration` and UI hard-limit fission |
| cli/architecture | `cli_commands/` and root CLI implementation | `cli-package-migration` and CLI hard-limit fission |
| integrations/architecture | SDK authoring root modules and installed SDK preview resources | `integration-sdk-package-migration` and post-1.0 resource-layout window |
| providers-harnesses/architecture | `protocols/`, native/provider target root modules, and over-budget native modules | provider package migration and hard-limit fission |
| automation-review/architecture | installed evidence resources | post-1.0 evidence resource-layout window |
| security/architecture | root permission, registry, settings, and secret policy implementations | security/settings context fission |

The exact paths and current line ceilings live in
`module-budgets.json`. Each over-budget executable module is pinned to its
current line count; stale exceptions fail the architecture gate.

## Consequences

- The compatibility allowlist contains import facades only, and architecture
  tests reject business definitions added to those facade modules.
- Existing target deviations cannot grow beyond their current module ceilings.
- A completed migration removes its deviation entry rather than renaming its
  gate.
- This ADR is not permission to add new behavior to a deviation. Such behavior
  must be introduced in the owning bounded context.
