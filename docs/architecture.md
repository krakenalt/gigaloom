# Architecture

GigaLoom is a standalone Python distribution with two local surfaces:

1. the `giga` command dispatches provider-native commands and administrative
   operations;
2. the FastAPI control plane serves the packaged browser Workbench on loopback.

Coding Agents are native coding CLIs, registry-installed ACP agents, or custom
structured routes. Automation Agents remain project-authored reusable workflow
agents under the existing `/api/agents` namespace. A registry listing is
discovery evidence only; it is never installation or execution authority.

## Main boundaries

- `gigaloom.harnesses` owns built-in adapters.
- `runtime` and `sessions` own jobs, leases, events, policy, and persistence.
- `project`, `workspace`, and `worktrees` bound filesystem mutations.
- `ui` projects redacted state; it does not become a second authority source.
- native provider CLIs own authentication and provider-side execution.
- optional gateway behavior enters through an installed distribution contract,
  not a source-tree dependency.

Actions bind the reviewed scope and preview to an approval. Dispatch revalidates
that binding and fails closed after drift, cancellation, lease loss, or missing
authority. Sensitive values are redacted before persistence and serialization.

## Detailed decisions

- [Harness component architecture](architecture/harness.md)
- [Package structure and module boundaries](architecture/package-structure.md)
- [Reliability, recovery, and performance](architecture/reliability-and-performance.md)
- [Authority and approvals](architecture/authority-and-approvals.md)
- [Controlled network access](architecture/network-access.md)
- [Limited GitHub permissions](architecture/github-permissions.md)
- [Provider authentication capabilities](architecture/provider-authentication.md)
- [Frontend asset build](architecture/frontend-assets.md)
- [Operational guarantees](architecture/operational-guarantees.md)
