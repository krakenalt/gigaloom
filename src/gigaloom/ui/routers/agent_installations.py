"""Install, probe, recovery, and lifecycle APIs for managed ACP agents."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import StreamingResponse

from gigaloom.harnesses.agent_profiles.installations import AgentInstallError
from gigaloom.harnesses.agent_profiles.onboarding import ManagedAcpProbeReceipt
from gigaloom.ui.async_execution import ContractAPIRouter, run_stream_offload
from gigaloom.ui.schemas.agent_registry import (
    AgentActivateRequest,
    AgentActivationResponse,
    AgentConfirmedActionRequest,
    AgentInstallPreviewRequest,
    AgentInstallPreviewResponse,
    AgentInstallStartRequest,
    AgentInstallationOperationResponse,
    AgentProbeResponse,
    AgentRecoveryResponse,
    AgentRemoveResponse,
    AgentRollbackResponse,
    AgentUpdateStartRequest,
    AgentUseResponse,
)
from gigaloom.ui.services.agent_installations import (
    AgentInstallationWebOperation,
    AgentInstallationWebService,
)


def create_router(service: AgentInstallationWebService) -> APIRouter:
    """Create the cohesive lifecycle router without browser-owned decisions."""
    router = ContractAPIRouter()

    @router.proc_read.post(
        "/api/agent-runtimes/installations/preview",
        response_model=AgentInstallPreviewResponse,
    )
    def preview(payload: AgentInstallPreviewRequest) -> AgentInstallPreviewResponse:
        try:
            result = service.preview(
                payload.registry_query,
                local_agent_id=payload.local_agent_id,
            )
        except AgentInstallError as error:
            raise HTTPException(status_code=409, detail=error.reason_code) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="managed agent preview was rejected"
            ) from error
        return _preview_response(result)

    @router.fs_atomic.post(
        "/api/agent-runtimes/installations",
        response_model=AgentInstallationOperationResponse,
    )
    def install(
        payload: AgentInstallStartRequest,
    ) -> AgentInstallationOperationResponse:
        try:
            operation = service.start_install(
                payload.registry_query,
                local_agent_id=payload.local_agent_id,
                expected_plan_id=payload.expected_plan_id,
                confirmed=payload.confirmed,
                allow_unverified=payload.allow_unverified,
            )
        except AgentInstallError as error:
            raise HTTPException(status_code=409, detail=error.reason_code) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="managed agent install was rejected"
            ) from error
        return _operation_response(operation)

    @router.fs_read.get(
        "/api/agent-runtimes/installations/{operation_id}",
        response_model=AgentInstallationOperationResponse,
    )
    def inspect_operation(
        operation_id: str = Path(min_length=9, max_length=64),
    ) -> AgentInstallationOperationResponse:
        try:
            return _operation_response(service.inspect_operation(operation_id))
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="managed agent operation was not found"
            ) from error

    @router.fs_atomic.post(
        "/api/agent-runtimes/installations/{operation_id}/cancel",
        response_model=AgentInstallationOperationResponse,
    )
    def cancel_operation(
        operation_id: str = Path(min_length=9, max_length=64),
    ) -> AgentInstallationOperationResponse:
        try:
            return _operation_response(service.cancel_operation(operation_id))
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="managed agent operation was not found"
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=409, detail="managed agent cancellation was rejected"
            ) from error

    @router.stream.get(
        "/api/agent-runtimes/installations/{operation_id}/events",
    )
    async def operation_events(
        operation_id: str = Path(min_length=9, max_length=64),
        after: int = Query(default=-1, ge=-1, le=63),
    ) -> StreamingResponse:
        try:
            service.inspect_operation(operation_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="managed agent operation was not found"
            ) from error

        async def stream_events():
            cursor = after
            while True:
                try:
                    events, terminal = await run_stream_offload(
                        service.wait_for_events,
                        operation_id,
                        cursor,
                    )
                except KeyError:
                    break
                for event in events:
                    cursor = event.sequence
                    yield _sse_event(event.sequence, asdict(event))
                if terminal and not events:
                    break
                if not events:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0)

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.fs_atomic.post(
        "/api/agent-runtimes/installations/recover",
        response_model=AgentRecoveryResponse,
    )
    def recover() -> AgentRecoveryResponse:
        value = service.recover()
        return AgentRecoveryResponse(
            recovered_operation_ids=list(value.recovered_operation_ids),
            recovered_plan_ids=list(value.recovered_plan_ids),
            cleanup_statuses=list(value.cleanup_statuses),
        )

    @router.fs_atomic.post(
        "/api/agent-runtimes/{local_agent_id}/update",
        response_model=AgentInstallationOperationResponse,
    )
    def update(
        payload: AgentUpdateStartRequest,
        local_agent_id: str = Path(min_length=1, max_length=256),
    ) -> AgentInstallationOperationResponse:
        try:
            value = service.start_update(
                local_agent_id,
                confirmed=payload.confirmed,
                allow_unverified=payload.allow_unverified,
            )
        except AgentInstallError as error:
            raise HTTPException(status_code=409, detail=error.reason_code) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="managed agent update was rejected"
            ) from error
        return _operation_response(value)

    @router.proc_read.post(
        "/api/agent-runtimes/{local_agent_id}/probe",
        response_model=AgentProbeResponse,
    )
    def probe(
        local_agent_id: str = Path(min_length=1, max_length=256),
    ) -> AgentProbeResponse:
        try:
            value = service.probe(local_agent_id)
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="managed agent probe was rejected"
            ) from error
        return _probe_response(
            value,
            readiness=service.readiness(local_agent_id, probe=value),
        )

    @router.fs_atomic.post(
        "/api/agent-runtimes/{local_agent_id}/activate",
        response_model=AgentActivationResponse,
    )
    def activate(
        payload: AgentActivateRequest,
        local_agent_id: str = Path(min_length=1, max_length=256),
    ) -> AgentActivationResponse:
        try:
            result = service.activate(
                local_agent_id,
                install_id=payload.install_id,
                confirmed=payload.confirmed,
            )
        except AgentInstallError as error:
            raise HTTPException(status_code=409, detail=error.reason_code) from error
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="managed agent activation was rejected"
            ) from error
        return AgentActivationResponse(
            local_agent_id=result.artifact.local_agent_id,
            registry_id=result.artifact.registry_id,
            version=result.artifact.version,
            install_id=result.artifact.install_id,
            active=result.active,
            activation_status=result.activation.status.value,
            compatibility_status=result.compatibility.status.value,
            probe=_probe_response(
                result.probe,
                readiness=service.readiness(local_agent_id, probe=result.probe),
            ),
            omissions=list(result.receipt.omissions),
            atomic=result.activation.atomic,
            content_free=True,
        )

    @router.fs_atomic.post(
        "/api/agent-runtimes/{local_agent_id}/rollback",
        response_model=AgentRollbackResponse,
    )
    def rollback(
        payload: AgentConfirmedActionRequest,
        local_agent_id: str = Path(min_length=1, max_length=256),
    ) -> AgentRollbackResponse:
        try:
            value = service.rollback(local_agent_id, confirmed=payload.confirmed)
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="managed agent rollback was rejected"
            ) from error
        return AgentRollbackResponse(
            local_agent_id=value.local_agent_id,
            install_id=value.install_id,
            previous_install_id=value.previous_install_id,
            status=value.status.value,
            atomic=value.atomic,
        )

    @router.fs_atomic.post(
        "/api/agent-runtimes/{local_agent_id}/remove",
        response_model=AgentRemoveResponse,
    )
    def remove(
        payload: AgentConfirmedActionRequest,
        local_agent_id: str = Path(min_length=1, max_length=256),
    ) -> AgentRemoveResponse:
        try:
            removed = service.remove(local_agent_id, confirmed=payload.confirmed)
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=409, detail="managed agent removal was rejected"
            ) from error
        return AgentRemoveResponse(
            local_agent_id=local_agent_id,
            removed_install_count=removed,
            native_or_provider_artifacts_removed=False,
        )

    @router.fs_read.get(
        "/api/agent-runtimes/{local_agent_id}/use",
        response_model=AgentUseResponse,
    )
    def use_in_new_run(
        local_agent_id: str = Path(min_length=1, max_length=256),
    ) -> AgentUseResponse:
        try:
            selected, href = service.use_in_new_run(local_agent_id)
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=404, detail="managed agent runtime was not found"
            ) from error
        return AgentUseResponse(local_agent_id=selected, href=href, run_started=False)

    return router


def _preview_response(result) -> AgentInstallPreviewResponse:  # noqa: ANN001
    plan = result.plan
    return AgentInstallPreviewResponse(
        reason_code=result.reason_code,
        local_agent_id=result.local_agent_id,
        proposed_local_agent_id=result.proposed_local_agent_id,
        collision_namespaces=list(result.collision_namespaces),
        decisions=[
            {
                "distribution_digest": item.distribution_digest,
                "rank": item.rank,
                "status": item.status.value,
                "reason_code": item.reason_code,
            }
            for item in result.decisions
        ],
        plan=(
            None
            if plan is None
            else {
                "plan_id": plan.plan_id,
                "registry_id": plan.registry_id,
                "entry_digest": plan.entry_digest,
                "snapshot_digest": plan.snapshot_digest,
                "local_agent_id": plan.local_agent_id,
                "version": plan.version,
                "platform": plan.platform,
                "architecture": plan.architecture,
                "distribution_kind": plan.distribution_kind.value,
                "package_or_archive": plan.package_or_archive,
                "integrity_policy": plan.integrity_policy.value,
                "lifecycle_script_policy": plan.lifecycle_script_policy.value,
                "side_effects": list(plan.side_effects),
                "confirmation_required": plan.confirmation_required,
                "expires_at": plan.expires_at.isoformat(),
            }
        ),
        installation_started=False,
        browser_selected_distribution=False,
    )


def _operation_response(
    value: AgentInstallationWebOperation,
) -> AgentInstallationOperationResponse:
    return AgentInstallationOperationResponse(
        **asdict(value),
        terminal=value.terminal,
    )


def _probe_response(
    value: ManagedAcpProbeReceipt,
    *,
    readiness,
) -> AgentProbeResponse:  # noqa: ANN001
    return AgentProbeResponse(
        state=value.state.value,
        protocol_state=value.protocol_state,
        protocol_version=value.protocol_version,
        auth_methods=list(value.auth_methods),
        capabilities=list(value.capabilities),
        losses=list(value.losses),
        warnings=list(value.warnings),
        native_home_isolated=value.native_home_isolated,
        network_policy=value.network_policy,
        readiness=asdict(readiness),
        content_free=True,
    )


def _sse_event(sequence: int, payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"id: {sequence}\nevent: progress\ndata: {data}\n\n"


__all__ = ["create_router"]
