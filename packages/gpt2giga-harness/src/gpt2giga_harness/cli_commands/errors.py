"""Shared CLI exception-to-exit-code mapping."""

from __future__ import annotations


def format_cli_error(exc: Exception) -> str | None:
    """Return the stable user-facing message for a handled CLI exception."""
    from gpt2giga_harness.evals import EvalSpecNotFoundError
    from gpt2giga_harness.integration_flows import (
        IntegrationFlowConflictError,
        IntegrationFlowError,
        IntegrationFlowNotFoundError,
    )
    from gpt2giga_harness.integration_groups import (
        IntegrationGroupConflictError,
        IntegrationGroupError,
        IntegrationGroupNotFoundError,
    )
    from gpt2giga_harness.native.registry import UnknownNativeHistoryConnectorError
    from gpt2giga_harness.project_memory import ProjectMemoryNotFoundError
    from gpt2giga_harness.provider_settings import (
        ProviderRegistryConflict,
        ProviderSettingsNotFoundError,
    )
    from gpt2giga_harness.registry import UnknownHarnessError
    from gpt2giga_harness.sessions import RunNotFoundError, SessionNotFoundError
    from gpt2giga_harness.ui.remote_identity import RemoteIdentityError

    unknown_errors = (
        (UnknownHarnessError, "Unknown harness"),
        (UnknownNativeHistoryConnectorError, "Unknown native harness"),
        (SessionNotFoundError, "Unknown session"),
        (RunNotFoundError, "Unknown run"),
        (ProjectMemoryNotFoundError, "Unknown memory"),
        (ProviderSettingsNotFoundError, "Unknown provider"),
        (EvalSpecNotFoundError, "Unknown eval"),
        (IntegrationFlowNotFoundError, "Unknown integration flow"),
        (IntegrationGroupNotFoundError, "Unknown integration group"),
    )
    for error_type, label in unknown_errors:
        if isinstance(exc, error_type):
            return f"{label}: {exc.args[0]}"
    if isinstance(exc, ProviderRegistryConflict):
        return f"Provider registry conflict: {exc}"
    if isinstance(
        exc,
        (
            IntegrationFlowConflictError,
            IntegrationFlowError,
            IntegrationGroupConflictError,
            IntegrationGroupError,
            RemoteIdentityError,
            ValueError,
        ),
    ):
        return str(exc)
    return None
