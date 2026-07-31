"""Canonical project catalog serialization."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from typing import Any, Mapping

from .models import (
    PROJECT_CATALOG_SCHEMA_VERSION,
    ProjectCatalogEntryV1,
    ProjectLocationRef,
)

_ENTRY_KEYS = {
    "schema_version",
    "catalog_project_id",
    "display_name",
    "harness_project_id",
    "location",
    "state",
    "created_at",
    "updated_at",
    "last_opened_at",
    "revision",
    "digest",
}
_LOCATION_KEYS = {"kind", "path", "canonical_path", "identity"}


def catalog_entry_to_dict(entry: ProjectCatalogEntryV1) -> dict[str, Any]:
    """Serialize one catalog entry to its stable JSON shape."""
    return {
        "schema_version": entry.schema_version,
        "catalog_project_id": entry.catalog_project_id,
        "display_name": entry.display_name,
        "harness_project_id": entry.harness_project_id,
        "location": {
            "kind": entry.location.kind,
            "path": entry.location.path,
            "canonical_path": entry.location.canonical_path,
            "identity": entry.location.identity,
        },
        "state": entry.state,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "last_opened_at": entry.last_opened_at,
        "revision": entry.revision,
        "digest": entry.digest,
    }


def catalog_entry_from_dict(data: Mapping[str, Any]) -> ProjectCatalogEntryV1:
    """Parse and verify one strict catalog entry payload."""
    if set(data) != _ENTRY_KEYS:
        raise ValueError("project catalog entry has unexpected fields")
    location = data.get("location")
    if not isinstance(location, Mapping) or set(location) != _LOCATION_KEYS:
        raise ValueError("project catalog location has unexpected fields")
    entry = ProjectCatalogEntryV1(
        schema_version=_required_int(data, "schema_version"),
        catalog_project_id=_required_text(data, "catalog_project_id"),
        display_name=_required_text(data, "display_name"),
        harness_project_id=_required_text(data, "harness_project_id"),
        location=ProjectLocationRef(
            kind=_required_text(location, "kind"),
            path=_optional_text(location.get("path")),
            canonical_path=_optional_text(location.get("canonical_path")),
            identity=_optional_text(location.get("identity")),
        ),
        state=_required_text(data, "state"),
        created_at=_required_text(data, "created_at"),
        updated_at=_required_text(data, "updated_at"),
        last_opened_at=_optional_text(data.get("last_opened_at")),
        revision=_required_int(data, "revision"),
        digest=_required_text(data, "digest"),
    )
    if entry.digest != catalog_entry_digest(entry):
        raise ValueError("project catalog entry digest mismatch")
    return entry


def with_catalog_entry_digest(
    entry: ProjectCatalogEntryV1,
) -> ProjectCatalogEntryV1:
    """Return an entry carrying its canonical SHA-256 digest."""
    return replace(entry, digest=catalog_entry_digest(entry))


def catalog_entry_digest(entry: ProjectCatalogEntryV1) -> str:
    """Hash the canonical entry payload without its self-referential digest."""
    payload = catalog_entry_to_dict(entry)
    payload["digest"] = ""
    return sha256(_canonical_json(payload)).hexdigest()


def encode_catalog_entry(entry: ProjectCatalogEntryV1) -> bytes:
    """Encode one verified catalog entry for atomic persistence."""
    if entry.schema_version != PROJECT_CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported project catalog schema_version")
    if entry.digest != catalog_entry_digest(entry):
        raise ValueError("project catalog entry digest mismatch")
    return _canonical_json(catalog_entry_to_dict(entry)) + b"\n"


def decode_catalog_entry(payload: bytes) -> ProjectCatalogEntryV1:
    """Decode one catalog entry while rejecting duplicate JSON keys."""
    try:
        data = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("project catalog entry is unreadable") from exc
    if not isinstance(data, dict):
        raise ValueError("project catalog entry must be an object")
    return catalog_entry_from_dict(data)


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("optional text values must be non-empty strings")
    return value


def _required_int(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value
