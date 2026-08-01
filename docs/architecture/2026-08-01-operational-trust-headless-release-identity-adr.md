# ADR: Operational trust, headless execution, and release identity

Status: accepted on 2026-08-01 for the GigaLoom 0.8 operational foundation.

## Context

GigaLoom 0.7 established a native Agent Gateway: coding agents own their real
terminal processes, while governed structured execution enters through an
explicit GigaLoom surface. The next operational layer needs stable vocabulary
and machine contracts before registry installation, recovery, upgrade, visual
evaluation, and headless execution can evolve independently.

Five ambiguities must be removed:

- `/api/agents` already describes project-authored automation definitions, so
  it cannot also name installed coding-agent runtimes;
- a registry entry can describe a structured ACP route without owning a native
  human-terminal command;
- reviewed executable versions are useful evidence but are too volatile to be
  the sole authority for structured compatibility;
- a machine runner needs deterministic streams and exit meanings rather than
  terminal presentation conventions;
- release identity is repeated across Python, npm, manifests, changelogs, and
  workflow artifact names without one hand-edited source.

This decision extends the existing authority, approval, project, run, route,
evidence, capsule, and Agent Profile owners. It does not create parallel state
owners.

## Decision

### Product vocabulary and API ownership

The following terms are distinct public concepts:

| Concept | Product label | API owner |
| --- | --- | --- |
| Native coding CLIs, registry-installed ACP agents, and custom structured routes | **Coding Agents** | `/api/agent-runtimes/...` |
| Official remote catalog of compatible ACP agents | **ACP Registry** | `/api/agent-runtimes/registry/...` |
| Managed, isolated downloaded or package artifacts | **Installed Agents** | `/api/agent-runtimes/installations/...` |
| Project-authored reusable workflow agents | **Automation Agents** | existing `/api/agents/...` |
| ACP, app-server, event streams, and similar machine transports | **Routes** | below one Coding Agent |

`registry_id`, `local_agent_id`, `native_agent_id`, `route_id`, and
`managed_install_id` remain separate typed identities. A default may copy a
registry id into a local id only after collision checks. Display names never
create aliases. Linking an installed ACP route to an existing native product
identity is a separate explicit action.

Registry metadata is discovery evidence, not execution authority. Search,
refresh, and installation preview do not install, authenticate, execute, open
a browser, mutate a native home, or grant workspace or network authority.

### Native suffix ownership

Once the first token resolves to a Coding Agent with a native launch contract,
every remaining token belongs to that native agent. GigaLoom forwards the
suffix without parsing, translation, injection, removal, persistence, or
content hashing. Native stdout, stderr, terminal topology, signals, and exit
status remain provider-owned under the existing direct or managed native
process contract.

Core commands always win namespace resolution. In particular,
`giga agent add ...` is the GigaLoom registry installer, while
`giga <native-agent> add ...` is opaque native-agent syntax. A registry id may
not shadow a core command, native id, or alias.

A registry-only ACP installation is not a native terminal command. It is used
through an explicit structured owner such as `giga run --agent <id>` or the Web
Workbench. GigaLoom never launches an ACP stdio server directly into a human
terminal merely because the registry contains that id.

### Protocol-first structured compatibility

Native launch eligibility is version-independent whenever the executable can
be resolved and launched safely. Structured-route admission instead evaluates
a non-mutating observation containing executable identity, protocol
negotiation, framing, mandatory capabilities, security invariants, and
reviewed evidence.

A reviewed version range affects confidence and warnings. It is not, by
itself, an allowlist. A newer or unknown executable that passes the required
protocol and capability probe is `compatible_unverified`; it does not lose its
structured route solely because the version string is outside the last
reviewed range.

Structured admission fails closed for:

- protocol-major incompatibility;
- a missing mandatory capability or session behavior;
- malformed or unsafe framing;
- a failed security invariant;
- an explicit known-bad rule bound to immutable evidence.

Probe failure, drift, or structured incompatibility never makes an otherwise
safe native `giga <agent>` invocation ineligible. Version strings, manifests,
signatures, package integrity, discovery, and download success remain evidence
and grant no installation, execution, authentication, credential, network, or
filesystem authority.

### Headless stdout, stderr, events, and exits

`giga run --headless` is the sole public headless execution owner. It accepts
exactly one explicit prompt source, requires `--no-input` semantics, has no TTY
dependency, and separately validates the workspace and result directory.

For `jsonl-v1` mode:

- stdout contains exactly one canonical JSON object per line and no other
  output;
- stderr contains only bounded human diagnostics;
- neither stream contains ANSI control sequences;
- event sequence numbers are monotonic within one run;
- signals and timeouts enter bounded cancellation and produce a terminal
  receipt;
- exactly one terminal event is emitted;
- the terminal event contains a result or capsule reference plus an explicit
  omission list.

The required event vocabulary is `run_started`, `agent_resolved`,
`route_observed`, `turn_started`, `tool_activity`, `approval_required`,
`usage`, `artifact`, `warning`, `run_succeeded`, `run_failed`, and
`run_canceled`. Success requires both process success and the required
structured terminal receipt.

Exit codes are stable:

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 2 | CLI usage or deterministic admission failure |
| 10 | Authentication required or expired |
| 20 | Policy or authority refusal |
| 30 | Coding Agent or structured-transport failure |
| 40 | Cancellation or timeout |
| 50 | State, evidence, or integrity failure |
| 70 | Internal invariant failure |

### Release identity ownership

`release/version.toml` is the only hand-edited release identity. The release
preparation command owns deterministic projections into Python metadata, npm
metadata, `release/release.json`, the Git tag, generated changelog sections,
generated package documentation, and the artifact manifest.

Preparation is atomic and idempotent. Verification fails when any projection
drifts, and a no-op preparation is byte-identical. Build hooks may verify
tracked inputs but may not silently mutate them.

Release evidence uses role-based names such as `external-evidence.json`,
`candidate-report.md`, and `artifact-set.toml`. The version belongs inside the
digest-bound payload, not in the filename. Tagging, registry publication, and
GitHub Release creation remain separate owner actions and are not implied by
preparation.

### Performance and privacy

Performance is part of each application contract. The Settings shell and
lightweight local defaults must become renderable without waiting for provider
account probing, MCP inventory, diagnostics, or every harness projection.
Registry refresh and installation progress must not enter CLI startup or
`giga --version`.

The pre-change, content-free baseline is committed at
[`benchmarks/gigaloom_performance/baselines/2026-08-01-operational-foundation.json`](https://github.com/krakenalt/gigaloom/blob/main/benchmarks/gigaloom_performance/baselines/2026-08-01-operational-foundation.json).
It records cold CLI, API, Settings, source-inventory, and release-bump facts at
the accepted source commit. Absolute timings are host evidence, not portable
budgets.

No secret, prompt, response, credential value, raw native argument, or private
path is part of the baseline or the compatibility observation. Credential
material remains reference-only until the last responsible injection boundary
and never becomes model-visible or persisted content.

## Consequences

Coding Agent and Automation Agent pages, routes, and state can now evolve
without overloading one noun or API namespace. Native commands remain durable
across structured-adapter drift, while structured transports still fail closed
on protocol, capability, integrity, and security failures.

Headless consumers can rely on one ANSI-free machine contract instead of
scraping terminal output. Release preparation can later replace manual
multi-file edits without granting publication authority.

This ADR changes no runtime dispatch, API response, persisted state, release
metadata, provider process, native home, or external system. Implementation
and migration require separately verified changes under the owners named
above.
