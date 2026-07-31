"""Launch profile lifecycle service."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

from gigaloom.projects.catalog.errors import ProjectCatalogConflictError
from gigaloom.projects.catalog.repository import FilesystemProjectCatalogRepository

from .codec import with_launch_profile_digest
from .models import (
    LaunchResolutionContextV1,
    ProjectLaunchProfileV1,
    ResolvedProjectLaunchProfileV1,
    TerminalModeHint,
)
from .repository import FilesystemLaunchProfileRepository
from .resolution import resolve_launch_profile

_UNSET = object()


class ProjectLaunchProfileService:
    """Manage soft launch hints independently of execution authority."""

    def __init__(
        self,
        repository: FilesystemLaunchProfileRepository,
        catalog_repository: FilesystemProjectCatalogRepository,
    ) -> None:
        self.repository = repository
        self.catalog_repository = catalog_repository

    def create_profile(
        self,
        catalog_project_id: str,
        *,
        display_name: str,
        agent_hint: str | None = None,
        structured_route_hint: str | None = None,
        model_hint: str | None = None,
        mode_hint: str | None = None,
        host_hint: str | None = None,
        workspace_policy_hint: str | None = None,
        terminal_mode_hint: TerminalModeHint | None = None,
    ) -> ProjectLaunchProfileV1:
        """Create a profile for a non-tombstoned catalog project."""
        project = self.catalog_repository.get(catalog_project_id)
        if project.state == "tombstoned":
            raise ProjectCatalogConflictError(
                "cannot create a launch profile for a tombstoned project"
            )
        profile = ProjectLaunchProfileV1(
            launch_profile_id=f"launch_{uuid4().hex[:24]}",
            catalog_project_id=catalog_project_id,
            display_name=_normalized(display_name, "display_name"),
            agent_hint=_normalized_optional(agent_hint, "agent_hint"),
            structured_route_hint=_normalized_optional(
                structured_route_hint, "structured_route_hint"
            ),
            model_hint=_normalized_optional(model_hint, "model_hint"),
            mode_hint=_normalized_optional(mode_hint, "mode_hint"),
            host_hint=_normalized_optional(host_hint, "host_hint"),
            workspace_policy_hint=_normalized_optional(
                workspace_policy_hint, "workspace_policy_hint"
            ),
            terminal_mode_hint=terminal_mode_hint,
            revision=1,
            digest="0" * 64,
        )
        return self.repository.create(with_launch_profile_digest(profile))

    def update_profile(
        self,
        launch_profile_id: str,
        *,
        expected_revision: int,
        display_name: str | object = _UNSET,
        agent_hint: str | None | object = _UNSET,
        structured_route_hint: str | None | object = _UNSET,
        model_hint: str | None | object = _UNSET,
        mode_hint: str | None | object = _UNSET,
        host_hint: str | None | object = _UNSET,
        workspace_policy_hint: str | None | object = _UNSET,
        terminal_mode_hint: TerminalModeHint | None | object = _UNSET,
    ) -> ProjectLaunchProfileV1:
        """Update explicit fields while preserving the project binding."""
        current = self.repository.get(launch_profile_id)
        if current.revision != expected_revision:
            raise ProjectCatalogConflictError("launch profile revision is stale")
        changes: dict[str, Any] = {
            "revision": current.revision + 1,
            "digest": "0" * 64,
        }
        for field, value in (
            ("display_name", display_name),
            ("agent_hint", agent_hint),
            ("structured_route_hint", structured_route_hint),
            ("model_hint", model_hint),
            ("mode_hint", mode_hint),
            ("host_hint", host_hint),
            ("workspace_policy_hint", workspace_policy_hint),
            ("terminal_mode_hint", terminal_mode_hint),
        ):
            if value is _UNSET:
                continue
            if field == "terminal_mode_hint":
                changes[field] = value
            elif field == "display_name":
                changes[field] = _normalized(str(value), field)
            else:
                changes[field] = _normalized_optional(value, field)
        updated = with_launch_profile_digest(replace(current, **changes))
        return self.repository.replace(updated, expected_revision=expected_revision)

    def delete_profile(
        self,
        launch_profile_id: str,
        *,
        expected_revision: int,
    ) -> ProjectLaunchProfileV1:
        """Delete only profile metadata."""
        return self.repository.delete(
            launch_profile_id,
            expected_revision=expected_revision,
        )

    def resolve_profile(
        self,
        launch_profile_id: str,
        context: LaunchResolutionContextV1,
    ) -> ResolvedProjectLaunchProfileV1:
        """Resolve a profile against explicit current inventory."""
        return resolve_launch_profile(self.repository.get(launch_profile_id), context)


def _normalized(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _normalized_optional(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text or null")
    return _normalized(value, field)
