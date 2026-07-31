"""Session metadata contract for catalog project bindings."""

from __future__ import annotations

from typing import Any, Mapping

CATALOG_PROJECT_ID_METADATA_KEY = "catalog_project_id"
LEGACY_PROJECT_ID_METADATA_KEY = "project_id"


def session_catalog_project_id(metadata: Mapping[str, Any]) -> str | None:
    """Read the canonical binding, falling back only for unmigrated sessions."""
    catalog_project_id = metadata.get(CATALOG_PROJECT_ID_METADATA_KEY)
    if isinstance(catalog_project_id, str) and catalog_project_id:
        return catalog_project_id
    legacy_project_id = metadata.get(LEGACY_PROJECT_ID_METADATA_KEY)
    if isinstance(legacy_project_id, str) and legacy_project_id:
        return legacy_project_id
    return None
