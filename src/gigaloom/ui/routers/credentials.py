"""Content-free credential source, lease request, and revocation APIs."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Path

from gigaloom.runtime.credentials import (
    CredentialLeaseNotFoundError,
    CredentialSourceNotFoundError,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.schemas.credentials import (
    CredentialLeaseRequest,
    CredentialOperatorActionResponse,
    CredentialOperatorSnapshotResponse,
    CredentialRevokeRequest,
)
from gigaloom.ui.services.credentials import CredentialOperatorService


def create_router(service: CredentialOperatorService) -> APIRouter:
    """Create one revision-bound router around the application-owned broker."""
    router = ContractAPIRouter()

    @router.fs_read.get(
        "/api/credentials",
        response_model=CredentialOperatorSnapshotResponse,
    )
    def snapshot() -> CredentialOperatorSnapshotResponse:
        return CredentialOperatorSnapshotResponse(**asdict(service.snapshot()))

    @router.fs_atomic.post(
        "/api/credentials/leases",
        response_model=CredentialOperatorActionResponse,
    )
    def request_lease(
        payload: CredentialLeaseRequest,
    ) -> CredentialOperatorActionResponse:
        try:
            result = service.request_lease(**payload.model_dump())
        except (CredentialSourceNotFoundError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail="credential lease request was rejected",
            ) from error
        return CredentialOperatorActionResponse(**asdict(result))

    @router.fs_atomic.post(
        "/api/credentials/leases/{lease_id}/revoke",
        response_model=CredentialOperatorActionResponse,
    )
    def revoke_lease(
        payload: CredentialRevokeRequest,
        lease_id: str = Path(min_length=1, max_length=256),
    ) -> CredentialOperatorActionResponse:
        try:
            result = service.revoke_lease(
                lease_id,
                expected_revision=payload.expected_revision,
            )
        except CredentialLeaseNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="credential lease was not found",
            ) from error
        return CredentialOperatorActionResponse(**asdict(result))

    return router


__all__ = ["create_router"]
