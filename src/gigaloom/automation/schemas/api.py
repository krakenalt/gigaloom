"""Public editor-schema automation facade."""

from .bindings import add_yaml_language_server_header as add_yaml_language_server_header
from .bindings import yaml_language_server_header as yaml_language_server_header
from .generator import all_artifact_schemas as all_artifact_schemas
from .generator import artifact_schema as artifact_schema
from .generator import render_artifact_schema as render_artifact_schema
from .models import ArtifactSchema as ArtifactSchema
from .models import ArtifactSchemaName as ArtifactSchemaName
from .resources import export_artifact_schema as export_artifact_schema
from .resources import packaged_schema_text as packaged_schema_text

__all__ = [
    "ArtifactSchema",
    "ArtifactSchemaName",
    "add_yaml_language_server_header",
    "all_artifact_schemas",
    "artifact_schema",
    "export_artifact_schema",
    "packaged_schema_text",
    "render_artifact_schema",
    "yaml_language_server_header",
]
