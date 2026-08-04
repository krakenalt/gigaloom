"""Dynamic Workbench adapters for active managed ACP runtimes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping

from gigaloom.harnesses.acp import (
    AcpLimits,
    AcpPermissionRequestV1,
    AcpRouteIdentity,
    begin_prompt,
    create_acp_client,
    new_session,
    next_permission,
    set_session_config,
)
from gigaloom.harnesses.acp.errors import (
    AcpError,
    AcpPermissionError,
    AcpRequestCancelled,
    AcpRequestTimeout,
)
from gigaloom.harnesses.acp.usage import AcpTokenUsageV1, usage_payload
from gigaloom.harnesses.acp.provider_bridge import (
    AcpGatewayRouteRequired,
    AcpProviderBridgeUnavailable,
    apply_provider_bridge_configuration,
    decode_resolved_gateway_route,
    pin_provider_bridge_process,
    require_provider_bridge_route,
    resolve_available_provider_bridge,
)
from gigaloom.harnesses.agent_profiles.installations import AgentRuntimeService
from gigaloom.harnesses.agent_profiles.onboarding import ManagedAgentOnboardingResult
from gigaloom.harnesses.base import BaseHarness
from gigaloom.harnesses.managed_acp_permissions import (
    ManagedAcpAuthenticationRequired,
    answer_permission,
    is_canceled,
    permission_context as build_permission_context,
)
from gigaloom.structured_processes import (
    NormalizedStructuredEvent,
    StructuredProcessError,
)
from gigaloom.native.api import ResolvedGatewayRoute
from gigaloom.types import (
    Availability,
    HarnessCapability,
    HarnessContext,
    HarnessEvent,
    HarnessEventType,
    HarnessRequest,
    HarnessResult,
    HarnessSpec,
    HeadlessContinuationStrategy,
    emit_event,
)


class ManagedAcpStateChanged(RuntimeError):
    """Raised when current runtime evidence no longer matches the active revision."""


@dataclass(frozen=True, slots=True)
class ManagedAcpTurnRequest:
    """One transient managed ACP turn with product-owned authority inputs."""

    agent_id: str
    route_id: str
    model_id: str
    workspace: str
    prompt: str
    run_id: str
    permission_profile_id: str
    network_profile: str
    timeout_seconds: float
    gateway_route: ResolvedGatewayRoute | None = None
    session_model_config_id: str | None = None
    gateway_api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ManagedAcpTurnResult:
    """Normalized transient output from one managed ACP turn."""

    stop_reason: str
    text: str
    usage: AcpTokenUsageV1 | None
    events: tuple[NormalizedStructuredEvent, ...]
    capability_snapshot_digest: str


def run_managed_acp_turn(
    record: ManagedAgentOnboardingResult,
    request: ManagedAcpTurnRequest,
    *,
    cancel_event: object | None = None,
    event_sink: Callable[[NormalizedStructuredEvent], None] | None = None,
    permission_sink: Callable[[AcpPermissionRequestV1], None] | None = None,
) -> ManagedAcpTurnResult:
    """Run one active, digest-bound ACP turn without persisting provider content."""
    if record.profile.agent_id != request.agent_id or not record.active:
        raise ManagedAcpStateChanged("managed ACP revision is not active")
    if request.timeout_seconds <= 0:
        raise ValueError("managed ACP timeout must be positive")
    artifact = record.artifact
    managed_root = Path(artifact.managed_root).resolve(strict=True)
    executable = (managed_root / artifact.executable_relative_path).resolve(strict=True)
    if not executable.is_relative_to(managed_root):
        raise ManagedAcpStateChanged("managed ACP executable escaped its root")
    workspace = Path(request.workspace).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("managed ACP workspace must be a directory")
    route = next(
        (
            item
            for item in record.profile.structured_routes
            if item.route_id == request.route_id
            and item.transport_kind == "acp_stdio_v1"
        ),
        None,
    )
    if route is None:
        raise ManagedAcpStateChanged("managed ACP route is unavailable")
    gateway_route = request.gateway_route
    resolution = resolve_available_provider_bridge(
        registry_id=artifact.registry_id,
        version=artifact.version,
        providers_advertised="provider_configuration" in record.probe.capabilities,
        route=gateway_route,
    )
    with tempfile.TemporaryDirectory(prefix="gigaloom-managed-acp-") as root:
        native_home = Path(root) / "home"
        native_home.mkdir(mode=0o700)
        environment = {
            "HOME": str(native_home),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TMPDIR": root,
            **dict(artifact.environment),
        }
        process, overlay = pin_provider_bridge_process(
            (str(executable), *artifact.arguments),
            cwd=workspace.as_posix(),
            environment=environment,
            resolution=resolution,
            route=gateway_route,
            api_key=request.gateway_api_key,
            isolated_root=Path(root),
        )
        client = create_acp_client(
            process,
            compatibility_profile_digest=record.profile.profile_digest,
            route_identity=AcpRouteIdentity(
                record.profile.agent_id,
                route.route_id,
                record.profile.profile_digest,
            ),
            limits=AcpLimits(
                request_timeout_seconds=min(request.timeout_seconds, 30.0),
            ),
        )
        events: list[NormalizedStructuredEvent] = []
        text_parts: list[str] = []
        try:
            client.start()
            snapshot = client.initialize()
            if snapshot.snapshot_digest != record.probe.capability_snapshot_digest:
                raise ManagedAcpStateChanged("managed ACP capability evidence changed")
            model_config_id = request.session_model_config_id
            model_value = request.model_id
            if gateway_route is not None and resolution is not None:
                model_config_id, capability_matches = (
                    apply_provider_bridge_configuration(
                        client,
                        snapshot,
                        gateway_route,
                        resolution,
                        overlay,
                        api_key=request.gateway_api_key,
                    )
                )
                if not capability_matches:
                    raise ManagedAcpStateChanged(
                        "managed ACP provider capability evidence changed"
                    )
                model_value = gateway_route.public_model_alias
            binding = new_session(client, workspace=workspace)
            if model_config_id is not None:
                set_session_config(
                    client,
                    binding,
                    config_id=model_config_id,
                    value=model_value,
                )
            prompt = begin_prompt(client, binding, text=request.prompt)
            permission_context = build_permission_context(request, binding)
            deadline = time.monotonic() + request.timeout_seconds
            while not prompt.done:
                if is_canceled(cancel_event):
                    prompt.cancel()
                    raise AcpRequestCancelled("managed ACP run was canceled")
                if time.monotonic() >= deadline:
                    prompt.cancel()
                    raise AcpRequestTimeout("managed ACP run timed out")
                pending = next_permission(
                    client,
                    binding,
                    permission_context,
                    timeout=0.02,
                )
                if pending is not None:
                    if permission_sink is not None:
                        permission_sink(pending)
                    answer_permission(client, binding, permission_context, pending)
                _consume_event(client, events, text_parts, event_sink, timeout=0.02)
            while _consume_event(
                client,
                events,
                text_parts,
                event_sink,
                timeout=0.01,
            ):
                pass
            result = prompt.result(timeout=max(0.001, deadline - time.monotonic()))
        finally:
            client.close()
    return ManagedAcpTurnResult(
        stop_reason=result.stop_reason,
        text="".join(text_parts),
        usage=result.usage,
        events=tuple(events),
        capability_snapshot_digest=snapshot.snapshot_digest,
    )


class ManagedAcpHarness(BaseHarness):
    """Expose one active managed ACP connector as a Workbench harness."""

    def __init__(
        self,
        runtime: AgentRuntimeService,
        record: ManagedAgentOnboardingResult,
    ) -> None:
        self._runtime = runtime
        self._agent_id = record.profile.agent_id
        self._spec = _managed_spec(record)

    def spec(self) -> HarnessSpec:
        """Return metadata for this exact managed agent identity."""
        return self._spec

    def availability(self) -> Availability:
        """Report current-pointer availability without probing or spawning."""
        try:
            record = self._active_record()
        except ManagedAcpStateChanged as exc:
            return Availability.missing("managed ACP runtime unavailable", str(exc))
        reason = f"active managed ACP runtime ({record.probe.state.value})"
        return Availability.available(reason)

    def run(
        self,
        request: HarnessRequest,
        context: HarnessContext,
    ) -> HarnessResult:
        """Execute one transient provider-owned ACP turn."""
        retained_events: list[HarnessEvent] = []
        try:
            record = self._active_record()
            routes = tuple(
                route
                for route in record.profile.structured_routes
                if route.transport_kind == "acp_stdio_v1"
            )
            if len(routes) != 1:
                raise ManagedAcpStateChanged("managed ACP route is unavailable")
            route = routes[0]
            artifact = record.artifact
            command = (
                str(Path(artifact.managed_root) / artifact.executable_relative_path),
                *artifact.arguments,
            )
            if request.extra.get("dry_run"):
                return HarnessResult(
                    ok=True,
                    text="dry run",
                    raw={
                        "agent_id": self._agent_id,
                        "route_id": route.route_id,
                        "provider_authentication_required": bool(
                            record.probe.auth_methods
                        ),
                    },
                    command=command,
                )
            workspace = str(request.workspace or "").strip()
            if not workspace:
                raise ValueError("managed ACP run requires a workspace")
            gateway = decode_resolved_gateway_route(
                request.extra.get("gateway_route_binding")
            )
            require_provider_bridge_route(
                registry_id=record.artifact.registry_id,
                version=record.artifact.version,
                providers_advertised=(
                    "provider_configuration" in record.probe.capabilities
                ),
                requested_model=request.model,
                route=gateway,
            )

            def forward(event: NormalizedStructuredEvent) -> None:
                projected = _project_event(event)
                if projected is not None and not emit_event(request, projected):
                    retained_events.append(projected)

            def forward_permission(value: AcpPermissionRequestV1) -> None:
                event = HarnessEvent(
                    type="approval_required",
                    message=f"Managed ACP requested {value.action_class}.",
                    payload={
                        "action_class": value.action_class,
                        "admissible": value.admissible,
                        "binding_digest": value.binding_digest,
                    },
                )
                if not emit_event(request, event):
                    retained_events.append(event)

            result = run_managed_acp_turn(
                record,
                ManagedAcpTurnRequest(
                    agent_id=self._agent_id,
                    route_id=route.route_id,
                    model_id=(
                        f"{gateway.gateway_id}/{gateway.public_model_alias}"
                        if gateway is not None
                        else "provider-default"
                    ),
                    workspace=workspace,
                    prompt=request.prompt,
                    run_id=request.run_id or f"managed-{self._agent_id}",
                    permission_profile_id=str(
                        request.extra.get("permission_profile") or "interactive"
                    ),
                    network_profile=str(
                        request.extra.get("network_profile") or "interactive"
                    ),
                    timeout_seconds=context.timeout_seconds,
                    gateway_route=gateway,
                    gateway_api_key=(context.api_key if gateway is not None else None),
                ),
                cancel_event=request.cancel_event,
                event_sink=forward,
                permission_sink=forward_permission,
            )
            if result.usage is not None:
                usage = HarnessEvent(
                    type=HarnessEventType.USAGE.value,
                    message="Managed ACP reported token usage.",
                    payload=usage_payload(result.usage),
                )
                if not emit_event(request, usage):
                    retained_events.append(usage)
            return HarnessResult(
                ok=True,
                text=result.text,
                raw={
                    "agent_id": self._agent_id,
                    "route_id": route.route_id,
                    "stop_reason": result.stop_reason,
                    "capability_snapshot_digest": (result.capability_snapshot_digest),
                    **(
                        {
                            "gateway_route_id": gateway.route_id,
                            "gateway_profile_id": gateway.gateway_id,
                            "gateway_model": gateway.public_model_alias,
                        }
                        if gateway is not None
                        else {}
                    ),
                },
                events=tuple(retained_events),
                command=command,
            )
        except AcpGatewayRouteRequired:
            return _managed_failure(
                self._agent_id,
                retained_events,
                "gpt2giga route is not ready. Start or reconnect the gateway, "
                "refresh routes, and preflight the selected model. The ACP "
                "provider default was not used.",
                reason_id="managed_acp_gateway_route_required",
            )
        except AcpProviderBridgeUnavailable as exc:
            return _managed_failure(
                self._agent_id,
                retained_events,
                "This ACP connector is native-only because it has no verified "
                "provider bridge for gpt2giga. Native provider launch remains "
                "available; the provider default was not used for this request.",
                reason_id=exc.reason_id,
            )
        except ManagedAcpStateChanged:
            return _managed_failure(
                self._agent_id,
                retained_events,
                "Managed ACP evidence changed. Run Probe only and reactivate the revision.",
            )
        except AcpPermissionError:
            return _managed_failure(
                self._agent_id,
                retained_events,
                "Managed ACP permission was denied by the selected permission profile.",
            )
        except AcpRequestCancelled:
            return _managed_failure(
                self._agent_id,
                retained_events,
                "Managed ACP run canceled.",
            )
        except AcpRequestTimeout:
            return _managed_failure(
                self._agent_id,
                retained_events,
                "Managed ACP run timed out.",
            )
        except (AcpError, StructuredProcessError, OSError, ValueError):
            return _managed_failure(
                self._agent_id,
                retained_events,
                "Managed ACP transport failed. Run Probe only and inspect the runtime.",
            )

    def _active_record(self) -> ManagedAgentOnboardingResult:
        try:
            record = self._runtime.inspect(self._agent_id)
        except (KeyError, StopIteration, ValueError) as exc:
            raise ManagedAcpStateChanged(
                "managed ACP runtime is not installed"
            ) from exc
        if not record.active:
            raise ManagedAcpStateChanged("managed ACP runtime is inactive")
        return record


def acp_harnesses(
    runtime: AgentRuntimeService,
) -> tuple[ManagedAcpHarness, ...]:
    """Project every currently active managed ACP runtime into Workbench."""
    return tuple(
        ManagedAcpHarness(runtime, runtime.inspect(item.local_agent_id))
        for item in runtime.list()
        if item.active
    )


def _managed_spec(record: ManagedAgentOnboardingResult) -> HarnessSpec:
    profile = record.profile
    return HarnessSpec(
        id=profile.agent_id,
        title=profile.display_name,
        kind="agent-cli",
        description="Managed ACP connector installed from the official registry",
        capabilities=(HarnessCapability.AGENT_CLI,),
        supports_model_selection=False,
        supports_api_mode_selection=False,
        supports_streaming=True,
        supports_structured_events=True,
        supports_cancellation=True,
        supports_workspace=True,
        tags=("agent", "managed", "acp"),
        metadata={
            "managed_agent": True,
            "registry_id": record.artifact.registry_id,
            "version": record.artifact.version,
            "distribution_kind": record.artifact.distribution_kind.value,
            "profile_digest": profile.profile_digest,
            "provider_bridge": record.probe.provider_bridge.projection(),
        },
        headless_continuation=HeadlessContinuationStrategy.ONE_SHOT,
    )


def _consume_event(
    client,
    events: list[NormalizedStructuredEvent],
    text_parts: list[str],
    sink: Callable[[NormalizedStructuredEvent], None] | None,
    *,
    timeout: float,
) -> bool:  # noqa: ANN001
    event = client.supervisor.next_event(timeout=timeout)
    if event is None:
        return False
    events.append(event)
    text = _event_text(event)
    if text:
        text_parts.append(text)
    if sink is not None:
        sink(event)
    return True


def _event_text(event: NormalizedStructuredEvent) -> str:
    if event.type != "message.agent.delta":
        return ""
    update = _mapping(event.payload.get("update"))
    content = _mapping(update.get("content"))
    text = content.get("text")
    return text if isinstance(text, str) else ""


def _project_event(event: NormalizedStructuredEvent) -> HarnessEvent | None:
    update = _mapping(event.payload.get("update"))
    if event.type == "message.agent.delta":
        delta = _event_text(event)
        return (
            HarnessEvent(
                type=HarnessEventType.MESSAGE_DELTA.value,
                message=delta,
                payload={"delta": delta},
            )
            if delta
            else None
        )
    if event.type == "thought.agent.delta":
        content = _mapping(update.get("content"))
        delta = content.get("text")
        return (
            HarnessEvent(
                type=HarnessEventType.REASONING_DELTA.value,
                message="Managed ACP reasoning update.",
                payload={"delta": delta, "kind": "model"},
            )
            if isinstance(delta, str) and delta
            else None
        )
    if event.type in {"tool.started", "tool.updated"}:
        status = str(update.get("status") or "running")
        event_type = (
            HarnessEventType.TOOL_CALL_FINISHED.value
            if status in {"completed", "failed"}
            else HarnessEventType.TOOL_CALL_STARTED.value
            if event.type == "tool.started"
            else HarnessEventType.TOOL_CALL_DELTA.value
        )
        tool_id = str(update.get("toolCallId") or "managed-acp-tool")
        return HarnessEvent(
            type=event_type,
            message=str(update.get("title") or "Managed ACP tool update."),
            payload={
                "tool_call_id": tool_id,
                "name": str(update.get("kind") or "tool"),
                "status": status,
            },
        )
    return None


def _managed_failure(
    agent_id: str,
    events: list[HarnessEvent],
    error: str,
    *,
    reason_id: str | None = None,
) -> HarnessResult:
    return HarnessResult(
        ok=False,
        text="",
        raw={
            "agent_id": agent_id,
            **({"reason_id": reason_id} if reason_id is not None else {}),
        },
        events=tuple(events),
        error=error,
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "ManagedAcpAuthenticationRequired",
    "ManagedAcpHarness",
    "ManagedAcpStateChanged",
    "ManagedAcpTurnRequest",
    "ManagedAcpTurnResult",
    "acp_harnesses",
    "run_managed_acp_turn",
]
