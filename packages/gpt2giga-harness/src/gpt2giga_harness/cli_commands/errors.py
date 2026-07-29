"""Shared CLI exception-to-exit-code mapping."""

from __future__ import annotations


_UNKNOWN_ERRORS = {
    "gpt2giga_harness.evals.EvalSpecNotFoundError": "Unknown eval",
    "gpt2giga_harness.integration_flows.IntegrationFlowNotFoundError": (
        "Unknown integration flow"
    ),
    "gpt2giga_harness.integration_groups.IntegrationGroupNotFoundError": (
        "Unknown integration group"
    ),
    "gpt2giga_harness.native.registry.UnknownNativeHistoryConnectorError": (
        "Unknown native harness"
    ),
    "gpt2giga_harness.project_memory.ProjectMemoryNotFoundError": "Unknown memory",
    "gpt2giga_harness.provider_settings.ProviderSettingsNotFoundError": (
        "Unknown provider"
    ),
    "gpt2giga_harness.registry.UnknownHarnessError": "Unknown harness",
    "gpt2giga_harness.sessions.store.RunNotFoundError": "Unknown run",
    "gpt2giga_harness.sessions.store.SessionNotFoundError": "Unknown session",
}
_CONFLICT_OR_DOMAIN_ERRORS = frozenset(
    {
        "gpt2giga_harness.integration_flows.IntegrationFlowConflictError",
        "gpt2giga_harness.integration_flows.IntegrationFlowError",
        "gpt2giga_harness.integration_groups.IntegrationGroupConflictError",
        "gpt2giga_harness.integration_groups.IntegrationGroupError",
        "gpt2giga_harness.ui.remote_identity.RemoteIdentityError",
    }
)
_PROVIDER_CONFLICT = "gpt2giga_harness.provider_registry.ProviderRegistryConflict"


def format_cli_error(exc: Exception) -> str | None:
    """Return the stable user-facing message for a handled CLI exception."""
    qualified_names = {
        f"{error_type.__module__}.{error_type.__qualname__}"
        for error_type in type(exc).__mro__
    }
    for qualified_name, label in _UNKNOWN_ERRORS.items():
        if qualified_name in qualified_names:
            return f"{label}: {exc.args[0]}"
    if _PROVIDER_CONFLICT in qualified_names:
        return f"Provider registry conflict: {exc}"
    if qualified_names & _CONFLICT_OR_DOMAIN_ERRORS or isinstance(exc, ValueError):
        return str(exc)
    return None
