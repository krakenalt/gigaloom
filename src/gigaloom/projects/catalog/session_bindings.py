"""Typed session move operations at the project catalog boundary."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .errors import ProjectCatalogConflictError
from .repository import FilesystemProjectCatalogRepository


class CatalogBoundSession(Protocol):
    """Minimum session projection needed for catalog binding operations."""

    id: str
    updated_at: str
    metadata: Mapping[str, Any]


class CatalogSessionStore(Protocol):
    """Atomic session port consumed without importing its repository owner."""

    def get_session(self, session_id: str) -> CatalogBoundSession: ...

    def update_session_if_revision(
        self,
        session_id: str,
        expected_updated_at: str,
        **patch: Any,
    ) -> CatalogBoundSession | None: ...


class SessionCatalogBindingService:
    """Move sessions between catalog groups or the explicit unfiled group."""

    def __init__(
        self,
        catalog_repository: FilesystemProjectCatalogRepository,
        session_store: CatalogSessionStore,
    ) -> None:
        self.catalog_repository = catalog_repository
        self.session_store = session_store

    def move_session(
        self,
        session_id: str,
        *,
        to_catalog_project_id: str | None,
        expected_updated_at: str,
    ) -> CatalogBoundSession:
        """Atomically bind a session without retaining the legacy project id."""
        if to_catalog_project_id is not None:
            project = self.catalog_repository.get(to_catalog_project_id)
            if project.state == "tombstoned":
                raise ProjectCatalogConflictError(
                    "cannot move a session to a tombstoned project"
                )
        current = self.session_store.get_session(session_id)
        if current.updated_at != expected_updated_at:
            raise ProjectCatalogConflictError("session revision is stale")
        metadata = dict(current.metadata)
        metadata.pop("project_id", None)
        if to_catalog_project_id is None:
            metadata.pop("catalog_project_id", None)
        else:
            metadata["catalog_project_id"] = to_catalog_project_id
        updated = self.session_store.update_session_if_revision(
            session_id,
            expected_updated_at,
            metadata=metadata,
        )
        if updated is None:
            raise ProjectCatalogConflictError("session revision is stale")
        return updated
