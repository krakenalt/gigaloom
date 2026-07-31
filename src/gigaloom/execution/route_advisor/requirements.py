"""Validated deterministic inputs for route admission."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.execution.route_advisor.models import (
    ROUTE_ADVISOR_SCHEMA_VERSION,
    RouteIntent,
    _validate_digest,
    _validate_identity,
    _validate_identity_sequence,
)


@dataclass(frozen=True)
class RouteRequirementsV1:
    """Authority and capability requirements derived without an LLM."""

    intent: RouteIntent
    required_capabilities: tuple[str, ...]
    required_transport_classes: tuple[str, ...]
    workspace_policy: str
    network_policy: str
    cost_policy_ref: str
    platform: str
    context_manifest_digest: str
    project_id: str
    launch_profile_digest: str | None = None
    preferred_route_id: str | None = None
    required_host_id: str | None = None
    required_account_digest: str | None = None
    require_known_cost: bool = False
    require_sealed_evaluation: bool = False
    require_session_portability: bool = False
    schema_version: int = ROUTE_ADVISOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_ADVISOR_SCHEMA_VERSION:
            raise ValueError("unsupported route requirements schema_version")
        if not isinstance(self.intent, RouteIntent):
            raise ValueError("route intent is invalid")
        _validate_identity_sequence(
            self.required_capabilities,
            field_name="required capabilities",
        )
        _validate_identity_sequence(
            self.required_transport_classes,
            field_name="required transport classes",
        )
        for value, field_name in (
            (self.workspace_policy, "workspace policy"),
            (self.network_policy, "network policy"),
            (self.cost_policy_ref, "cost policy ref"),
            (self.platform, "platform"),
            (self.project_id, "project id"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_digest(
            self.context_manifest_digest,
            field_name="context manifest digest",
        )
        if self.launch_profile_digest is not None:
            _validate_digest(
                self.launch_profile_digest,
                field_name="launch profile digest",
            )
        if self.preferred_route_id is not None:
            _validate_identity(self.preferred_route_id, field_name="preferred route id")
        if self.required_host_id is not None:
            _validate_identity(self.required_host_id, field_name="required host id")
        if self.required_account_digest is not None:
            _validate_digest(
                self.required_account_digest,
                field_name="required account digest",
            )


__all__ = ["RouteRequirementsV1"]
