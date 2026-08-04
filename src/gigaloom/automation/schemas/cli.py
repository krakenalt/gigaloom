"""Route-local CLI metadata and handlers for editor-schema export."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from .generator import all_artifact_schemas, artifact_schema
from .models import ArtifactSchemaName
from .resources import export_artifact_schema, packaged_schema_text


def register_schema_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the bounded ``giga schema`` family on a supplied parser."""
    schema = subparsers.add_parser("schema")
    commands = schema.add_subparsers(dest="schema_command")

    list_command = commands.add_parser("list")
    list_command.add_argument("--json", action="store_true")
    list_command.set_defaults(handler="_handle_schema_list")

    for name in ArtifactSchemaName:
        export = commands.add_parser(name.value)
        export.add_argument("--output", type=Path)
        export.set_defaults(
            handler="_handle_schema_export",
            artifact_schema_name=name.value,
        )


def _handle_schema_list(args: argparse.Namespace, _config: Any = None) -> int:
    payload = {
        "schema_version": 1,
        "schemas": [_schema_summary(item.name) for item in all_artifact_schemas()],
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        for item in payload["schemas"]:
            sys.stdout.write(
                f"{item['name']:<18}{item['filename']:<30}{item['project_glob']}\n"
            )
    return 0


def _handle_schema_export(args: argparse.Namespace, _config: Any = None) -> int:
    name = ArtifactSchemaName(args.artifact_schema_name)
    if args.output is None:
        sys.stdout.write(packaged_schema_text(name))
    else:
        export_artifact_schema(name, args.output)
    return 0


def _schema_summary(name: ArtifactSchemaName) -> dict[str, Any]:
    descriptor = artifact_schema(name)
    text = packaged_schema_text(name)
    return {
        "name": name.value,
        "filename": descriptor.filename,
        "project_glob": descriptor.project_glob,
        "schema_version": descriptor.schema_version,
        "schema_id": descriptor.document["$id"],
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


__all__ = [
    "_handle_schema_export",
    "_handle_schema_list",
    "register_schema_commands",
]
