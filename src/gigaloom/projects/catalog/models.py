"""Stable project catalog value objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal

PROJECT_CATALOG_SCHEMA_VERSION = 1
MAX_CATALOG_ENTRIES = 10_000
MAX_CATALOG_PAGE_SIZE = 100
MAX_PROJECT_DISPLAY_NAME_CHARS = 200

ProjectCatalogState = Literal["active", "unresolved", "tombstoned"]

_CATALOG_ID_PATTERN = re.compile(r"^prj_[a-f0-9]{24}$")
_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ProjectLocationRef:
    """Content-free local project location and observed filesystem identity."""

    kind: Literal["local"]
    path: str | None
    canonical_path: str | None
    identity: str | None

    def __post_init__(self) -> None:
        if self.kind != "local":
            raise ValueError("unsupported project location kind")
        if (self.path is None) != (self.canonical_path is None):
            raise ValueError("project location paths must both be present or absent")
        for value in (self.path, self.canonical_path):
            if value is not None and not Path(value).is_absolute():
                raise ValueError("project location paths must be absolute")
        if self.identity is not None and not _DIGEST_PATTERN.fullmatch(self.identity):
            raise ValueError("project location identity must be a SHA-256 digest")


@dataclass(frozen=True)
class ProjectCatalogEntryV1:
    """One durable project catalog entry."""

    catalog_project_id: str
    display_name: str
    harness_project_id: str
    location: ProjectLocationRef
    state: ProjectCatalogState
    created_at: str
    updated_at: str
    last_opened_at: str | None
    revision: int
    digest: str
    schema_version: Literal[1] = PROJECT_CATALOG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROJECT_CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported project catalog schema_version")
        if not _CATALOG_ID_PATTERN.fullmatch(self.catalog_project_id):
            raise ValueError("invalid catalog_project_id")
        name = self.display_name.strip()
        if not name or len(name) > MAX_PROJECT_DISPLAY_NAME_CHARS:
            raise ValueError("project display_name is empty or too long")
        if self.display_name != name or any(ord(char) < 32 for char in name):
            raise ValueError("project display_name is not normalized")
        if not self.harness_project_id.strip():
            raise ValueError("harness_project_id is required")
        if self.state not in {"active", "unresolved", "tombstoned"}:
            raise ValueError("invalid project catalog state")
        if self.state == "active" and (
            self.location.canonical_path is None or self.location.identity is None
        ):
            raise ValueError("active project location must be resolved")
        if self.revision < 1:
            raise ValueError("project catalog revision must be positive")
        if not _DIGEST_PATTERN.fullmatch(self.digest):
            raise ValueError("project catalog digest must be a SHA-256 digest")


@dataclass(frozen=True)
class ProjectCatalogPageV1:
    """One bounded deterministic catalog page."""

    items: tuple[ProjectCatalogEntryV1, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class ProjectRelocationPreviewV1:
    """Explicit old/new project location comparison before mutation."""

    catalog_project_id: str
    expected_revision: int
    old_location: ProjectLocationRef
    new_location: ProjectLocationRef
    same_location: bool
    identity_matches: bool
    preview_digest: str
