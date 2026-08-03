"""Validated access and export for packaged editor schemas."""

from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
from typing import Any, Mapping

from .generator import artifact_schema, render_artifact_schema
from .models import ArtifactSchemaName


_ASSET_PACKAGE = "gigaloom.automation.schemas.assets"


def packaged_schema_text(name: ArtifactSchemaName | str) -> str:
    """Load one packaged schema and fail if it drifted from its generator."""
    descriptor = artifact_schema(name)
    try:
        text = (
            files(_ASSET_PACKAGE)
            .joinpath(descriptor.filename)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            f"Packaged editor schema is unavailable: {descriptor.filename}"
        ) from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Packaged editor schema is invalid: {descriptor.filename}"
        ) from exc
    if not isinstance(document, Mapping) or text != render_artifact_schema(name):
        raise RuntimeError(
            f"Packaged editor schema drifted from its generator: {descriptor.filename}"
        )
    return text


def packaged_schema_document(
    name: ArtifactSchemaName | str,
) -> Mapping[str, Any]:
    """Return one validated packaged schema document."""
    document = json.loads(packaged_schema_text(name))
    if not isinstance(document, Mapping):  # pragma: no cover - guarded above
        raise RuntimeError("Packaged editor schema must be a JSON object")
    return document


def export_artifact_schema(
    name: ArtifactSchemaName | str,
    output: str | Path,
) -> Path:
    """Write one exact packaged schema to an explicit local destination."""
    target = Path(output).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(packaged_schema_text(name), encoding="utf-8")
    return target
