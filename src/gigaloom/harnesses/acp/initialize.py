"""ACP v1 initialize request and response validation."""

from __future__ import annotations

from acp.schema import (
    AuthCapabilities,
    BooleanConfigOptionCapabilities,
    ClientCapabilities,
    ClientSessionCapabilities,
    FileSystemCapabilities,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    PlanCapabilities,
    SessionConfigOptionsCapabilities,
)
from pydantic import ValidationError

from gigaloom.harnesses.acp.capabilities import build_capability_snapshot
from gigaloom.harnesses.acp.contracts import (
    ACP_PROTOCOL_VERSION,
    AcpCapabilitySnapshotV1,
    AcpClientInfo,
    AcpProcessSpec,
)
from gigaloom.harnesses.acp.errors import AcpProtocolError, AcpProtocolVersionError
from gigaloom.structured_processes import StructuredProcessSupervisor


def initialize_connection(
    supervisor: StructuredProcessSupervisor,
    spec: AcpProcessSpec,
    *,
    client_info: AcpClientInfo,
    compatibility_profile_digest: str,
    timeout: float,
) -> AcpCapabilitySnapshotV1:
    """Negotiate wire v1 and freeze capabilities for the current generation."""
    request = InitializeRequest(
        protocol_version=ACP_PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(
            fs=FileSystemCapabilities(
                read_text_file=False,
                write_text_file=False,
            ),
            terminal=False,
            session=ClientSessionCapabilities(
                config_options=SessionConfigOptionsCapabilities(
                    boolean=BooleanConfigOptionCapabilities()
                )
            ),
            plan=PlanCapabilities(),
            auth=AuthCapabilities(terminal=False),
        ),
        client_info=Implementation(
            name=client_info.name,
            title=client_info.title,
            version=client_info.version,
        ),
    )
    result = supervisor.request(
        "initialize",
        request.model_dump(mode="json", by_alias=True, exclude_none=True),
        timeout=timeout,
    )
    try:
        response = InitializeResponse.model_validate(result)
    except ValidationError as exc:
        raise AcpProtocolError(
            "ACP initialize response failed schema validation"
        ) from exc
    if response.protocol_version != ACP_PROTOCOL_VERSION:
        raise AcpProtocolVersionError("ACP agent selected an unsupported wire version")
    return build_capability_snapshot(
        response,
        client_info=client_info,
        compatibility_profile_digest=compatibility_profile_digest,
        process_fingerprint=spec.executable.fingerprint,
        connection_generation=supervisor.generation,
    )
