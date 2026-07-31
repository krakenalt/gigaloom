"""Project catalog lifecycle and explicit relocation service."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Callable
from uuid import uuid4

from gigaloom.projects.resolution import project_id_for_root

from .codec import with_catalog_entry_digest
from .errors import ProjectCatalogConflictError
from .models import (
    MAX_PROJECT_DISPLAY_NAME_CHARS,
    ProjectCatalogEntryV1,
    ProjectLocationRef,
    ProjectRelocationPreviewV1,
)
from .repository import FilesystemProjectCatalogRepository


class ProjectCatalogService:
    """Apply bounded project catalog mutations without touching repositories."""

    def __init__(
        self,
        repository: FilesystemProjectCatalogRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def add_project(
        self,
        path: str | Path,
        *,
        display_name: str,
    ) -> ProjectCatalogEntryV1:
        """Catalog an existing local directory, including one with no sessions."""
        location = resolved_project_location(path)
        now = _utc_text(self._clock())
        entry = ProjectCatalogEntryV1(
            catalog_project_id=f"prj_{uuid4().hex[:24]}",
            display_name=normalize_display_name(display_name),
            harness_project_id=project_id_for_root(location.canonical_path or ""),
            location=location,
            state="active",
            created_at=now,
            updated_at=now,
            last_opened_at=None,
            revision=1,
            digest="0" * 64,
        )
        return self.repository.create(with_catalog_entry_digest(entry))

    def rename_project(
        self,
        catalog_project_id: str,
        display_name: str,
        *,
        expected_revision: int,
    ) -> ProjectCatalogEntryV1:
        """Change display metadata without changing project identity or location."""
        current = self.repository.get(catalog_project_id)
        self._assert_revision(current, expected_revision)
        updated = replace(
            current,
            display_name=normalize_display_name(display_name),
            updated_at=_utc_text(self._clock()),
            revision=current.revision + 1,
            digest="0" * 64,
        )
        return self.repository.replace(
            with_catalog_entry_digest(updated),
            expected_revision=expected_revision,
        )

    def preview_relocation(
        self,
        catalog_project_id: str,
        new_path: str | Path,
        *,
        expected_revision: int,
    ) -> ProjectRelocationPreviewV1:
        """Resolve and compare a relocation before any catalog mutation."""
        current = self.repository.get(catalog_project_id)
        self._assert_revision(current, expected_revision)
        new_location = resolved_project_location(new_path)
        payload = {
            "catalog_project_id": catalog_project_id,
            "expected_revision": expected_revision,
            "old_location": _location_payload(current.location),
            "new_location": _location_payload(new_location),
        }
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ProjectRelocationPreviewV1(
            catalog_project_id=catalog_project_id,
            expected_revision=expected_revision,
            old_location=current.location,
            new_location=new_location,
            same_location=_location_key(current.location)
            == _location_key(new_location),
            identity_matches=(
                current.location.identity is not None
                and current.location.identity == new_location.identity
            ),
            preview_digest=digest,
        )

    def relocate_project(
        self,
        preview: ProjectRelocationPreviewV1,
        *,
        preview_digest: str,
        allow_identity_change: bool = False,
    ) -> ProjectCatalogEntryV1:
        """Apply the exact reviewed relocation while preserving catalog identity."""
        if preview.preview_digest != preview_digest:
            raise ProjectCatalogConflictError("project relocation preview is stale")
        current = self.repository.get(preview.catalog_project_id)
        self._assert_revision(current, preview.expected_revision)
        if current.location != preview.old_location:
            raise ProjectCatalogConflictError("project location changed after preview")
        if not preview.identity_matches and not allow_identity_change:
            raise ProjectCatalogConflictError(
                "project filesystem identity changed; explicit confirmation required"
            )
        updated = replace(
            current,
            harness_project_id=project_id_for_root(
                preview.new_location.canonical_path or ""
            ),
            location=preview.new_location,
            state="active",
            updated_at=_utc_text(self._clock()),
            revision=current.revision + 1,
            digest="0" * 64,
        )
        return self.repository.replace(
            with_catalog_entry_digest(updated),
            expected_revision=preview.expected_revision,
        )

    def remove_project(
        self,
        catalog_project_id: str,
        *,
        expected_revision: int,
    ) -> ProjectCatalogEntryV1:
        """Tombstone catalog metadata without deleting project or session state."""
        current = self.repository.get(catalog_project_id)
        self._assert_revision(current, expected_revision)
        updated = replace(
            current,
            state="tombstoned",
            updated_at=_utc_text(self._clock()),
            revision=current.revision + 1,
            digest="0" * 64,
        )
        return self.repository.replace(
            with_catalog_entry_digest(updated),
            expected_revision=expected_revision,
        )

    @staticmethod
    def _assert_revision(
        entry: ProjectCatalogEntryV1,
        expected_revision: int,
    ) -> None:
        if entry.revision != expected_revision:
            raise ProjectCatalogConflictError("project catalog revision is stale")


def resolved_project_location(path: str | Path) -> ProjectLocationRef:
    """Resolve an existing directory into a stable content-free location ref."""
    requested = Path(path).expanduser()
    try:
        canonical = requested.resolve(strict=True)
        stat_result = canonical.stat()
    except (FileNotFoundError, OSError) as exc:
        raise ValueError("project location must be an accessible directory") from exc
    if not canonical.is_dir():
        raise ValueError("project location must be a directory")
    identity = sha256(
        f"{stat_result.st_dev}\0{stat_result.st_ino}".encode()
    ).hexdigest()
    return ProjectLocationRef(
        kind="local",
        path=str(requested.absolute()),
        canonical_path=str(canonical),
        identity=identity,
    )


def normalize_display_name(value: str) -> str:
    """Validate and normalize a user-visible catalog name."""
    name = str(value).strip()
    if not name or len(name) > MAX_PROJECT_DISPLAY_NAME_CHARS:
        raise ValueError("project display_name is empty or too long")
    if any(ord(char) < 32 for char in name):
        raise ValueError("project display_name contains control characters")
    return name


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("project catalog clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _location_payload(location: ProjectLocationRef) -> dict[str, str | None]:
    return {
        "kind": location.kind,
        "path": location.path,
        "canonical_path": location.canonical_path,
        "identity": location.identity,
    }


def _location_key(location: ProjectLocationRef) -> str | None:
    value = location.canonical_path
    return os.path.normcase(value).casefold() if value is not None else None
