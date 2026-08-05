# Launch troubleshooting

Compatibility failures return `reason_ids`. They identify a check that did not
pass; they are not instructions to disable the check. Fix the cause and repeat
the dry-run or probe.

| Reason | Meaning | Check | Safe action |
|---|---|---|---|
| `gateway_not_installed` | The `gpt2giga` package or executable is missing | `giga doctor` and the GigaLoom installation method | Reinstall the published `gigaloom[gpt2giga]` package |
| `gateway_incompatible` | The gateway version or API is outside the supported window | The `gpt2giga` version and configuration inspection | Install a compatible version; do not override the check |
| `gateway_models_unavailable` | The gateway did not return a valid model list | `/models`, the gateway address, and authentication | Repair the gateway or credentials and explicitly refresh the route |
| `agent_has_no_configurable_provider_contract` | The ACP agent cannot safely change its model provider | `provider_bridge.status` in `inspect` | Launch with the native provider, without `--with` or `--model` |
| `acp_provider_adapter_version_mismatch` | The built-in adapter is not verified for this agent version | The installed version and `probe` result | Upgrade or roll back to a supported version, then probe again |
| `acp_provider_set_rejected` | The agent rejected or did not confirm the selected model provider | The supported protocol and probe readback | Fix the agent configuration or use native launch; do not enable an unverified fallback |
| `acp_model_selector_unavailable` | The agent accepted the provider but cannot select the requested model | Model-selection capability in `inspect` | Use a supported agent version or native launch |
| `gateway_upstream_credentials_unavailable` | GigaChat credentials are absent from the current process | Authentication variables in the same terminal | Export credentials and repeat the dry-run without printing secrets |
| `gateway_sidecar_port_busy` | Another process owns the managed gateway port | The port owner and active gateway profiles | Stop the known process or select an explicitly configured external gateway |
| `gateway_route_catalog_stale` | Stored model or capability information is stale | Gateway reachability and the last refresh time | Restore connectivity and refresh explicitly; stale data cannot authorize a launch |

A concrete JSON response can use a more specific reason in the same class.
Do not publish complete diagnostics until you have checked them for local paths
and secrets.

## Quick checks

Inspect GigaLoom and the agent:

```sh
giga doctor
giga agent list --json
giga agent inspect AGENT_ID --json
giga agent probe AGENT_ID --json
```

Inspect a route without launching the agent:

```sh
giga --with gpt2giga --model MODEL_ALIAS --dry-run --json AGENT_ID
```

Return to the agent's native model provider:

```sh
giga AGENT_ID
```

This rollback does not require restoring the agent's native configuration,
because GigaLoom does not modify it. For local service, backup, or state-upgrade
problems, continue with [Operations](operations.md).
