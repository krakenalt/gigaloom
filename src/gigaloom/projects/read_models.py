"""Bounded Project Catalog and Launch Profile read composition."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Protocol

from .catalog.models import ProjectCatalogEntryV1
from .launch_profiles.models import LaunchProfilePageV1, ProjectLaunchProfileV1


PROJECT_LAUNCH_READ_MODEL_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


class ProjectCatalogReadPort(Protocol):
    """Minimum public project reader required for composition."""

    def get(self, catalog_project_id: str) -> ProjectCatalogEntryV1: ...


class LaunchProfileReadPort(Protocol):
    """Minimum bounded launch-profile reader required for composition."""

    def list_page(
        self,
        catalog_project_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> LaunchProfilePageV1: ...


@dataclass(frozen=True)
class ProjectLaunchReadModelV1:
    """One catalog project and one bounded stable launch-profile page."""

    project: ProjectCatalogEntryV1
    launch_profiles: tuple[ProjectLaunchProfileV1, ...]
    next_cursor: str | None
    has_more: bool
    digest: str
    schema_version: int = PROJECT_LAUNCH_READ_MODEL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROJECT_LAUNCH_READ_MODEL_SCHEMA_VERSION:
            raise ValueError("unsupported project launch read model schema_version")
        if not isinstance(self.project, ProjectCatalogEntryV1):
            raise ValueError("project launch read model project is invalid")
        profile_ids = tuple(item.launch_profile_id for item in self.launch_profiles)
        if profile_ids != tuple(sorted(set(profile_ids))):
            raise ValueError("launch profiles must be sorted and unique")
        if any(
            item.catalog_project_id != self.project.catalog_project_id
            for item in self.launch_profiles
        ):
            raise ValueError("launch profile belongs to another catalog project")
        if not isinstance(self.has_more, bool):
            raise ValueError("project launch read model has_more is invalid")
        if (
            not isinstance(self.digest, str)
            or _DIGEST_RE.fullmatch(self.digest) is None
        ):
            raise ValueError("project launch read model digest is invalid")


class ProjectLaunchReadService:
    """Compose repositories through read-only ports without execution authority."""

    def __init__(
        self,
        catalog: ProjectCatalogReadPort,
        launch_profiles: LaunchProfileReadPort,
    ) -> None:
        self.catalog = catalog
        self.launch_profiles = launch_profiles

    def read(
        self,
        catalog_project_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ProjectLaunchReadModelV1:
        """Return one bounded aggregate with a deterministic content digest."""
        project = self.catalog.get(catalog_project_id)
        page = self.launch_profiles.list_page(
            catalog_project_id,
            cursor=cursor,
            limit=limit,
        )
        payload = {
            "schema_version": PROJECT_LAUNCH_READ_MODEL_SCHEMA_VERSION,
            "project": {
                "id": project.catalog_project_id,
                "digest": project.digest,
                "revision": project.revision,
                "state": project.state,
            },
            "launch_profiles": [
                {
                    "id": profile.launch_profile_id,
                    "digest": profile.digest,
                    "revision": profile.revision,
                }
                for profile in page.items
            ],
            "next_cursor": page.next_cursor,
            "has_more": page.has_more,
        }
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ProjectLaunchReadModelV1(
            project=project,
            launch_profiles=page.items,
            next_cursor=page.next_cursor,
            has_more=page.has_more,
            digest=digest,
        )


__all__ = [
    "PROJECT_LAUNCH_READ_MODEL_SCHEMA_VERSION",
    "LaunchProfileReadPort",
    "ProjectCatalogReadPort",
    "ProjectLaunchReadModelV1",
    "ProjectLaunchReadService",
]
