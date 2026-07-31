"""Standalone Project Catalog router ready for integration-owner mounting."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query

from gigaloom.projects.api import (
    ProjectCatalogConflictError,
    ProjectCatalogNotFoundError,
)
from gigaloom.sessions import SessionNotFoundError
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.schemas.projects import (
    ProjectCatalogDetailResponse,
    ProjectCatalogEntryResponse,
    ProjectCatalogPageResponse,
    ProjectCreateRequest,
    ProjectLaunchProfileCreateRequest,
    ProjectLaunchProfileResponse,
    ProjectLaunchProfileUpdateRequest,
    ProjectLaunchResolutionResponse,
    ProjectRelocateRequest,
    ProjectRelocationPreviewRequest,
    ProjectRelocationPreviewResponse,
    ProjectRenameRequest,
    ProjectSessionMoveRequest,
    ProjectSessionSummaryResponse,
)
from gigaloom.ui.services.project_catalog import (
    ProjectCatalogWebDetail,
    ProjectCatalogWebPage,
    ProjectCatalogWebService,
    ProjectCatalogWebSummary,
)


CatalogProjectId = Annotated[str, Path(pattern=r"^prj_[0-9a-f]{24}$")]
LaunchProfileId = Annotated[str, Path(pattern=r"^launch_[0-9a-f]{24}$")]


def create_router(service: ProjectCatalogWebService) -> APIRouter:
    """Create B2 routes without mutating the central Web router registry."""
    router = ContractAPIRouter()

    @router.fs_read.get(
        "/api/project-catalog",
        response_model=ProjectCatalogPageResponse,
    )
    def list_projects(
        cursor: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        include_tombstoned: bool = Query(default=False),
    ) -> ProjectCatalogPageResponse:
        return _page_response(
            service.list_projects(
                cursor=cursor,
                limit=limit,
                include_tombstoned=include_tombstoned,
            )
        )

    @router.fs_read.get(
        "/api/project-catalog/{catalog_project_id}",
        response_model=ProjectCatalogDetailResponse,
    )
    def project_detail(
        catalog_project_id: CatalogProjectId,
        profile_cursor: str | None = Query(default=None),
        profile_limit: int = Query(default=50, ge=1, le=100),
    ) -> ProjectCatalogDetailResponse:
        try:
            return _detail_response(
                service.detail(
                    catalog_project_id,
                    profile_cursor=profile_cursor,
                    profile_limit=profile_limit,
                )
            )
        except (ProjectCatalogNotFoundError, ValueError) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.post(
        "/api/project-catalog",
        response_model=ProjectCatalogEntryResponse,
    )
    def create_project(payload: ProjectCreateRequest) -> ProjectCatalogEntryResponse:
        try:
            return _summary_response(
                service.create_project(payload.path, payload.display_name)
            )
        except (ProjectCatalogConflictError, ValueError) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.patch(
        "/api/project-catalog/{catalog_project_id}",
        response_model=ProjectCatalogEntryResponse,
    )
    def rename_project(
        catalog_project_id: CatalogProjectId,
        payload: ProjectRenameRequest,
    ) -> ProjectCatalogEntryResponse:
        try:
            return _summary_response(
                service.rename_project(
                    catalog_project_id,
                    display_name=payload.display_name,
                    expected_revision=payload.expected_revision,
                )
            )
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.fs_read.post(
        "/api/project-catalog/{catalog_project_id}/relocation-preview",
        response_model=ProjectRelocationPreviewResponse,
    )
    def preview_relocation(
        catalog_project_id: CatalogProjectId,
        payload: ProjectRelocationPreviewRequest,
    ) -> ProjectRelocationPreviewResponse:
        try:
            return ProjectRelocationPreviewResponse.model_validate(
                asdict(
                    service.preview_relocation(
                        catalog_project_id,
                        new_path=payload.new_path,
                        expected_revision=payload.expected_revision,
                    )
                )
            )
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.post(
        "/api/project-catalog/{catalog_project_id}/relocate",
        response_model=ProjectCatalogEntryResponse,
    )
    def relocate_project(
        catalog_project_id: CatalogProjectId,
        payload: ProjectRelocateRequest,
    ) -> ProjectCatalogEntryResponse:
        try:
            return _summary_response(
                service.relocate_project(
                    catalog_project_id,
                    new_path=payload.new_path,
                    expected_revision=payload.expected_revision,
                    preview_digest=payload.preview_digest,
                    confirm_identity_change=payload.confirm_identity_change,
                )
            )
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.delete(
        "/api/project-catalog/{catalog_project_id}",
        response_model=ProjectCatalogEntryResponse,
    )
    def remove_project(
        catalog_project_id: CatalogProjectId,
        expected_revision: int = Query(ge=1),
    ) -> ProjectCatalogEntryResponse:
        try:
            return _summary_response(
                service.remove_project(
                    catalog_project_id,
                    expected_revision=expected_revision,
                )
            )
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.post(
        "/api/project-catalog/sessions/{session_id}/move",
        response_model=ProjectSessionSummaryResponse,
    )
    def move_session(
        session_id: Annotated[str, Path(min_length=1, max_length=128)],
        payload: ProjectSessionMoveRequest,
    ) -> ProjectSessionSummaryResponse:
        try:
            session = service.move_session(
                session_id,
                to_catalog_project_id=payload.to_catalog_project_id,
                expected_updated_at=payload.expected_updated_at,
            )
            project_id = session.metadata.get("catalog_project_id")
            return ProjectSessionSummaryResponse(
                id=session.id,
                title=session.title,
                updated_at=session.updated_at,
                catalog_project_id=(
                    project_id if isinstance(project_id, str) else None
                ),
            )
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            SessionNotFoundError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.post(
        "/api/project-catalog/{catalog_project_id}/launch-profiles",
        response_model=ProjectLaunchProfileResponse,
    )
    def create_profile(
        catalog_project_id: CatalogProjectId,
        payload: ProjectLaunchProfileCreateRequest,
    ) -> ProjectLaunchProfileResponse:
        try:
            profile = service.create_profile(
                catalog_project_id,
                **payload.model_dump(),
            )
            return ProjectLaunchProfileResponse.model_validate(asdict(profile))
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.patch(
        "/api/project-launch-profiles/{launch_profile_id}",
        response_model=ProjectLaunchProfileResponse,
    )
    def update_profile(
        launch_profile_id: LaunchProfileId,
        payload: ProjectLaunchProfileUpdateRequest,
    ) -> ProjectLaunchProfileResponse:
        values = payload.model_dump(exclude_unset=True)
        expected_revision = values.pop("expected_revision")
        try:
            profile = service.update_profile(
                launch_profile_id,
                expected_revision=expected_revision,
                changes=values,
            )
            return ProjectLaunchProfileResponse.model_validate(asdict(profile))
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    @router.fs_atomic.delete(
        "/api/project-launch-profiles/{launch_profile_id}",
        response_model=ProjectLaunchProfileResponse,
    )
    def delete_profile(
        launch_profile_id: LaunchProfileId,
        expected_revision: int = Query(ge=1),
    ) -> ProjectLaunchProfileResponse:
        try:
            profile = service.delete_profile(
                launch_profile_id,
                expected_revision=expected_revision,
            )
            return ProjectLaunchProfileResponse.model_validate(asdict(profile))
        except (
            ProjectCatalogNotFoundError,
            ProjectCatalogConflictError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc

    return router


def _summary_response(
    summary: ProjectCatalogWebSummary,
) -> ProjectCatalogEntryResponse:
    return ProjectCatalogEntryResponse.model_validate(
        {
            **asdict(summary.project),
            "session_count": summary.session_count,
            "session_count_truncated": summary.session_count_truncated,
        }
    )


def _page_response(page: ProjectCatalogWebPage) -> ProjectCatalogPageResponse:
    return ProjectCatalogPageResponse(
        projects=[_summary_response(item) for item in page.projects],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


def _detail_response(
    detail: ProjectCatalogWebDetail,
) -> ProjectCatalogDetailResponse:
    return ProjectCatalogDetailResponse(
        project=_summary_response(detail.project),
        launch_profiles=[
            ProjectLaunchProfileResponse.model_validate(asdict(item))
            for item in detail.launch_profiles
        ],
        launch_resolutions=[
            ProjectLaunchResolutionResponse.model_validate(asdict(item))
            for item in detail.launch_resolutions
        ],
        sessions=[
            ProjectSessionSummaryResponse(
                id=item.id,
                title=item.title,
                updated_at=item.updated_at,
                catalog_project_id=(
                    project_id
                    if isinstance(
                        project_id := item.metadata.get("catalog_project_id"),
                        str,
                    )
                    else None
                ),
            )
            for item in detail.sessions
        ],
        sessions_truncated=detail.sessions_truncated,
        next_profile_cursor=detail.next_profile_cursor,
        has_more_profiles=detail.has_more_profiles,
    )


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ProjectCatalogNotFoundError, SessionNotFoundError)):
        status_code = 404
    elif isinstance(exc, ProjectCatalogConflictError):
        status_code = 409
    else:
        status_code = 400
    return HTTPException(status_code=status_code, detail=str(exc))


__all__ = ["create_router"]
