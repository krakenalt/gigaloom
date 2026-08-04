"""Production composition for reviewed gateway operator diagnostics."""

from __future__ import annotations

from gigaloom.cli_commands.gateway_transport import (
    AuthenticatedGatewayMachineTransport,
    is_managed_gateway_endpoint,
)
from gigaloom.cli_commands.handlers.gateway import GatewayCommandService
from gigaloom.config import HarnessConfig
from gigaloom.native.launch.gateway_contracts import GatewayMode
from gigaloom.native.launch.gateway_discovery import GatewayRouteDiscovery
from gigaloom.native.launch.gateway_profile import (
    resolve_installed_gpt2giga_artifact,
    reviewed_gpt2giga_profile,
)
from gigaloom.native.launch.gateway_sidecar import (
    UrlLibGatewayStartupReadinessProbe,
)


def build_gateway_command_service(config: HarnessConfig) -> GatewayCommandService:
    """Build network-bounded diagnostics without starting persistent processes."""
    mode = (
        GatewayMode.MANAGED
        if config.auto_start_proxy and is_managed_gateway_endpoint(config.proxy_url)
        else GatewayMode.EXTERNAL
    )
    profile = reviewed_gpt2giga_profile(base_url=config.proxy_url, mode=mode)
    transport = AuthenticatedGatewayMachineTransport(config.api_key)
    return GatewayCommandService.create(
        (profile,),
        discovery=GatewayRouteDiscovery(transport),
        readiness_probe=UrlLibGatewayStartupReadinessProbe(transport),
        artifact_resolver=resolve_installed_gpt2giga_artifact,
    )


__all__ = ["build_gateway_command_service"]
