"""Shared CLI exception-to-exit-code mapping."""

from __future__ import annotations


_UNKNOWN_ERRORS = {
    "gigaloom.evals.EvalSpecNotFoundError": "Unknown eval",
    "gigaloom.integration_flows.IntegrationFlowNotFoundError": (
        "Unknown integration flow"
    ),
    "gigaloom.integration_groups.IntegrationGroupNotFoundError": (
        "Unknown integration group"
    ),
    "gigaloom.native.registry.UnknownNativeHistoryConnectorError": (
        "Unknown native harness"
    ),
    "gigaloom.project_memory.ProjectMemoryNotFoundError": "Unknown memory",
    "gigaloom.provider_settings.ProviderSettingsNotFoundError": ("Unknown provider"),
    "gigaloom.registry.UnknownHarnessError": "Unknown harness",
    "gigaloom.sessions.store.RunNotFoundError": "Unknown run",
    "gigaloom.sessions.store.SessionNotFoundError": "Unknown session",
}
_CONFLICT_OR_DOMAIN_ERRORS = frozenset(
    {
        "gigaloom.integration_flows.IntegrationFlowConflictError",
        "gigaloom.integration_flows.IntegrationFlowError",
        "gigaloom.integration_groups.IntegrationGroupConflictError",
        "gigaloom.integration_groups.IntegrationGroupError",
        "gigaloom.ui.remote_identity.RemoteIdentityError",
    }
)
_PROVIDER_CONFLICT = "gigaloom.provider_registry.ProviderRegistryConflict"


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
