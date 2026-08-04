"""Editor bindings that stay dependency-light for starter renderers."""

from __future__ import annotations


_FILENAMES = {
    "agent": "agent.schema.json",
    "workflow": "workflow.schema.json",
    "eval": "eval.schema.json",
    "schedule-source": "schedule-source.schema.json",
}


def yaml_language_server_header(name: str, *, schema_ref: str | None = None) -> str:
    """Return the local-schema header for one ``.giga`` YAML family."""
    try:
        filename = _FILENAMES[name]
    except KeyError as exc:
        raise ValueError(f"unknown artifact schema: {name}") from exc
    reference = schema_ref or f"../schemas/{filename}"
    return f"# yaml-language-server: $schema={reference}\n"


def add_yaml_language_server_header(
    content: str,
    name: str,
    *,
    schema_ref: str | None = None,
) -> str:
    """Add one deterministic YLS header without duplicating an existing one."""
    if content.startswith("# yaml-language-server: $schema="):
        return content
    return yaml_language_server_header(name, schema_ref=schema_ref) + content
