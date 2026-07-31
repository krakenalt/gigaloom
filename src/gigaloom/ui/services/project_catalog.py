"""Application service for bounded Project Catalog Web projections."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    ProjectCatalogEntryV1,
    ProjectCatalogPageV1,
    ProjectCatalogService,
    ProjectLaunchProfileService,
    ProjectLaunchProfileV1,
    ProjectRelocationPreviewV1,
)


MAX_PROJECT_SESSION_COUNT = 10_000


class ProjectSessionSummary(Protocol):
    """Minimum session view used for project grouping counts."""

    metadata: Mapping[str, Any]


class ProjectSessionReadPort(Protocol):
    """Bounded public session query consumed by the Web application layer."""

    def list_sessions(
        self,
        *,
        include_archived: bool = False,
        limit: int | None = None,
    ) -> tuple[ProjectSessionSummary, ...]: ...


@dataclass(frozen=True, slots=True)
class ProjectCatalogWebSummary:
    """One catalog entry and its bounded derived session count."""

    project: ProjectCatalogEntryV1
    session_count: int
    session_count_truncated: bool


@dataclass(frozen=True, slots=True)
class ProjectCatalogWebPage:
    """One stable catalog page with derived grouping counts."""

    projects: tuple[ProjectCatalogWebSummary, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True, slots=True)
class ProjectCatalogWebDetail:
    """One project and one bounded stable launch-profile page."""

    project: ProjectCatalogWebSummary
    launch_profiles: tuple[ProjectLaunchProfileV1, ...]
    next_profile_cursor: str | None
    has_more_profiles: bool


class ProjectCatalogWebService:
    """Compose catalog, profile, and session ports for the browser workspace."""

    def __init__(
        self,
        *,
        catalog_repository: FilesystemProjectCatalogRepository,
        profile_repository: FilesystemLaunchProfileRepository,
        session_store: ProjectSessionReadPort,
    ) -> None:
        self.catalog_repository = catalog_repository
        self.profile_repository = profile_repository
        self.catalog_service = ProjectCatalogService(catalog_repository)
        self.profile_service = ProjectLaunchProfileService(
            profile_repository,
            catalog_repository,
        )
        self.session_store = session_store

    @classmethod
    def from_data_dir(
        cls,
        data_dir: str | Path,
        *,
        session_store: ProjectSessionReadPort,
    ) -> ProjectCatalogWebService:
        """Build the service from the canonical runtime project state root."""
        root = Path(data_dir) / "projects"
        return cls(
            catalog_repository=FilesystemProjectCatalogRepository(root / "catalog"),
            profile_repository=FilesystemLaunchProfileRepository(
                root / "launch_profiles"
            ),
            session_store=session_store,
        )

    def list_projects(
        self,
        *,
        cursor: str | None,
        limit: int,
        include_tombstoned: bool,
    ) -> ProjectCatalogWebPage:
        """Return one page and derive all counts from one bounded session scan."""
        page = self.catalog_repository.list_page(
            cursor=cursor,
            limit=limit,
            include_tombstoned=include_tombstoned,
        )
        return self._with_counts(page)

    def detail(
        self,
        catalog_project_id: str,
        *,
        profile_cursor: str | None,
        profile_limit: int,
    ) -> ProjectCatalogWebDetail:
        """Return one project and one profile page without execution authority."""
        project = self.catalog_repository.get(catalog_project_id)
        profiles = self.profile_repository.list_page(
            catalog_project_id,
            cursor=profile_cursor,
            limit=profile_limit,
        )
        counts, truncated = self._session_counts()
        return ProjectCatalogWebDetail(
            project=ProjectCatalogWebSummary(
                project=project,
                session_count=counts[project.catalog_project_id],
                session_count_truncated=truncated,
            ),
            launch_profiles=profiles.items,
            next_profile_cursor=profiles.next_cursor,
            has_more_profiles=profiles.has_more,
        )

    def create_project(self, path: str, display_name: str) -> ProjectCatalogWebSummary:
        """Create catalog metadata without creating a session."""
        project = self.catalog_service.add_project(path, display_name=display_name)
        return ProjectCatalogWebSummary(project, 0, False)

    def rename_project(
        self,
        catalog_project_id: str,
        *,
        display_name: str,
        expected_revision: int,
    ) -> ProjectCatalogWebSummary:
        """Rename display metadata only."""
        project = self.catalog_service.rename_project(
            catalog_project_id,
            display_name,
            expected_revision=expected_revision,
        )
        counts, truncated = self._session_counts()
        return ProjectCatalogWebSummary(
            project,
            counts[project.catalog_project_id],
            truncated,
        )

    def preview_relocation(
        self,
        catalog_project_id: str,
        *,
        new_path: str,
        expected_revision: int,
    ) -> ProjectRelocationPreviewV1:
        """Return a digest-bound old/new location comparison."""
        return self.catalog_service.preview_relocation(
            catalog_project_id,
            new_path,
            expected_revision=expected_revision,
        )

    def relocate_project(
        self,
        catalog_project_id: str,
        *,
        new_path: str,
        expected_revision: int,
        preview_digest: str,
        confirm_identity_change: bool,
    ) -> ProjectCatalogWebSummary:
        """Recompute and apply the exact relocation preview."""
        preview = self.preview_relocation(
            catalog_project_id,
            new_path=new_path,
            expected_revision=expected_revision,
        )
        project = self.catalog_service.relocate_project(
            preview,
            preview_digest=preview_digest,
            allow_identity_change=confirm_identity_change,
        )
        counts, truncated = self._session_counts()
        return ProjectCatalogWebSummary(
            project,
            counts[project.catalog_project_id],
            truncated,
        )

    def remove_project(
        self,
        catalog_project_id: str,
        *,
        expected_revision: int,
    ) -> ProjectCatalogWebSummary:
        """Tombstone only catalog metadata."""
        project = self.catalog_service.remove_project(
            catalog_project_id,
            expected_revision=expected_revision,
        )
        counts, truncated = self._session_counts()
        return ProjectCatalogWebSummary(
            project,
            counts[project.catalog_project_id],
            truncated,
        )

    def create_profile(
        self,
        catalog_project_id: str,
        **values: Any,
    ) -> ProjectLaunchProfileV1:
        """Create one soft launch profile."""
        return self.profile_service.create_profile(catalog_project_id, **values)

    def update_profile(
        self,
        launch_profile_id: str,
        *,
        expected_revision: int,
        changes: Mapping[str, Any],
    ) -> ProjectLaunchProfileV1:
        """Update only explicitly presented profile fields."""
        if not changes:
            raise ValueError("launch profile update requires a changed field")
        if "display_name" in changes and changes["display_name"] is None:
            raise ValueError("launch profile display_name cannot be null")
        return self.profile_service.update_profile(
            launch_profile_id,
            expected_revision=expected_revision,
            **changes,
        )

    def delete_profile(
        self,
        launch_profile_id: str,
        *,
        expected_revision: int,
    ) -> ProjectLaunchProfileV1:
        """Delete profile metadata only."""
        return self.profile_service.delete_profile(
            launch_profile_id,
            expected_revision=expected_revision,
        )

    def _with_counts(self, page: ProjectCatalogPageV1) -> ProjectCatalogWebPage:
        counts, truncated = self._session_counts()
        return ProjectCatalogWebPage(
            projects=tuple(
                ProjectCatalogWebSummary(
                    item,
                    counts[item.catalog_project_id],
                    truncated,
                )
                for item in page.items
            ),
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def _session_counts(self) -> tuple[Counter[str], bool]:
        sessions = self.session_store.list_sessions(
            include_archived=True,
            limit=MAX_PROJECT_SESSION_COUNT + 1,
        )
        truncated = len(sessions) > MAX_PROJECT_SESSION_COUNT
        counts: Counter[str] = Counter()
        for session in sessions[:MAX_PROJECT_SESSION_COUNT]:
            project_id = session.metadata.get("catalog_project_id")
            if isinstance(project_id, str):
                counts[project_id] += 1
        return counts, truncated


__all__ = [
    "MAX_PROJECT_SESSION_COUNT",
    "ProjectCatalogWebDetail",
    "ProjectCatalogWebPage",
    "ProjectCatalogWebService",
    "ProjectCatalogWebSummary",
]
