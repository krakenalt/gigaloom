"""Bounded HTTP schemas for Project Catalog and Launch Profiles."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProjectLocationResponse(BaseModel):
    """Content-free local project location projection."""

    kind: Literal["local"]
    path: str | None
    canonical_path: str | None
    identity: str | None


class ProjectCatalogEntryResponse(BaseModel):
    """Browser-facing catalog entry with a bounded session count."""

    schema_version: Literal[1]
    catalog_project_id: str
    display_name: str
    harness_project_id: str
    location: ProjectLocationResponse
    state: Literal["active", "unresolved", "tombstoned"]
    created_at: str
    updated_at: str
    last_opened_at: str | None
    revision: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_count: int = Field(ge=0, le=10_000)
    session_count_truncated: bool


class ProjectCatalogPageResponse(BaseModel):
    """One stable bounded project page."""

    projects: list[ProjectCatalogEntryResponse]
    next_cursor: str | None
    has_more: bool


class ProjectLaunchProfileResponse(BaseModel):
    """Soft launch hints without credentials, arguments, or authority."""

    schema_version: Literal[1]
    launch_profile_id: str
    catalog_project_id: str
    display_name: str
    agent_hint: str | None
    structured_route_hint: str | None
    model_hint: str | None
    mode_hint: str | None
    host_hint: str | None
    workspace_policy_hint: str | None
    terminal_mode_hint: Literal["auto", "managed", "direct"] | None
    revision: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectCatalogDetailResponse(BaseModel):
    """One project plus one bounded launch-profile page."""

    project: ProjectCatalogEntryResponse
    launch_profiles: list[ProjectLaunchProfileResponse]
    next_profile_cursor: str | None
    has_more_profiles: bool


class ProjectCreateRequest(BaseModel):
    """Explicit empty-or-existing project catalog creation request."""

    path: str = Field(min_length=1, max_length=4096)
    display_name: str = Field(min_length=1, max_length=200)


class ProjectRenameRequest(BaseModel):
    """Optimistic display-only rename request."""

    display_name: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)


class ProjectRelocationPreviewRequest(BaseModel):
    """Resolve and compare a new location without mutation."""

    new_path: str = Field(min_length=1, max_length=4096)
    expected_revision: int = Field(ge=1)


class ProjectRelocationPreviewResponse(BaseModel):
    """Digest-bound old/new location review."""

    catalog_project_id: str
    expected_revision: int = Field(ge=1)
    old_location: ProjectLocationResponse
    new_location: ProjectLocationResponse
    same_location: bool
    identity_matches: bool
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectRelocateRequest(ProjectRelocationPreviewRequest):
    """Apply an exact relocation preview."""

    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_identity_change: bool = False


class ProjectLaunchProfileCreateRequest(BaseModel):
    """Create soft project defaults without authority or hidden arguments."""

    display_name: str = Field(min_length=1, max_length=200)
    agent_hint: str | None = Field(default=None, max_length=200)
    structured_route_hint: str | None = Field(default=None, max_length=200)
    model_hint: str | None = Field(default=None, max_length=200)
    mode_hint: str | None = Field(default=None, max_length=200)
    host_hint: str | None = Field(default=None, max_length=200)
    workspace_policy_hint: str | None = Field(default=None, max_length=200)
    terminal_mode_hint: Literal["auto", "managed", "direct"] | None = None


class ProjectLaunchProfileUpdateRequest(BaseModel):
    """Optimistic partial update; explicit null clears an optional hint."""

    expected_revision: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    agent_hint: str | None = Field(default=None, max_length=200)
    structured_route_hint: str | None = Field(default=None, max_length=200)
    model_hint: str | None = Field(default=None, max_length=200)
    mode_hint: str | None = Field(default=None, max_length=200)
    host_hint: str | None = Field(default=None, max_length=200)
    workspace_policy_hint: str | None = Field(default=None, max_length=200)
    terminal_mode_hint: Literal["auto", "managed", "direct"] | None = None
