"""Generate JSON Schemas from the current automation parser contracts."""

from __future__ import annotations

import json
from typing import Any, Mapping

from gigaloom.automation.agents.api import (
    AGENT_ID_PATTERN,
    ALLOWED_MODES,
    ALLOWED_WORKSPACE_POLICIES,
)
from gigaloom.automation.evaluations.api import CHECK_TYPES
from gigaloom.automation.ports import PermissionAction
from gigaloom.automation.workflows.api import (
    HANDOFF_ARTIFACT_TYPES,
    MAX_FAN_OUT,
    MAX_WORKFLOW_STEPS,
    WORKFLOW_ID_PATTERN,
    WorkflowStepKind,
)
from gigaloom.types import GigaChatApiMode, HarnessCapability

from .models import ArtifactSchema, ArtifactSchemaName


JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
EDITOR_SCHEMA_VERSION = 1
SCHEMA_ID_ROOT = "https://schemas.gigaloom.dev/automation/v1"


def all_artifact_schemas() -> tuple[ArtifactSchema, ...]:
    """Return every supported schema in stable CLI/display order."""
    return tuple(artifact_schema(name) for name in ArtifactSchemaName)


def artifact_schema(name: ArtifactSchemaName | str) -> ArtifactSchema:
    """Generate one schema without reading project or user state."""
    selected = ArtifactSchemaName(name)
    filename = f"{selected.value}.schema.json"
    builders = {
        ArtifactSchemaName.AGENT: (_agent_schema, ".giga/agents/*.{yaml,yml}"),
        ArtifactSchemaName.WORKFLOW: (
            _workflow_schema,
            ".giga/workflows/*.{yaml,yml}",
        ),
        ArtifactSchemaName.EVAL: (_eval_schema, ".giga/evals/*.{yaml,yml}"),
        ArtifactSchemaName.SCHEDULE_SOURCE: (
            _schedule_source_schema,
            ".giga/schedule-sources/*.{yaml,yml}",
        ),
    }
    builder, project_glob = builders[selected]
    return ArtifactSchema(
        name=selected,
        filename=filename,
        project_glob=project_glob,
        schema_version=EDITOR_SCHEMA_VERSION,
        document=builder(filename),
    )


def render_artifact_schema(name: ArtifactSchemaName | str) -> str:
    """Render one schema with deterministic key order and final newline."""
    return (
        json.dumps(
            artifact_schema(name).document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _root_schema(
    filename: str,
    *,
    title: str,
    description: str,
    properties: Mapping[str, Any],
    required: tuple[str, ...],
    additional_properties: bool,
    definitions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": f"{SCHEMA_ID_ROOT}/{filename}",
        "title": title,
        "description": description,
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": additional_properties,
        "x-gigaloom-schema-version": EDITOR_SCHEMA_VERSION,
    }
    if definitions:
        schema["$defs"] = dict(definitions)
    return schema


def _agent_schema(filename: str) -> dict[str, Any]:
    non_empty_array = _string_array(min_items=1)
    path_array = {
        **_string_array(),
        "description": "Project-relative paths only; absolute, home, and parent traversal paths are rejected.",
    }
    tool_array = {
        **_string_array(),
        "items": {"type": "string", "minLength": 1, "maxLength": 200},
    }
    properties: dict[str, Any] = {
        "id": {
            "type": "string",
            "pattern": AGENT_ID_PATTERN.pattern,
            "description": "Stable profile id; the filename stem must match this value.",
        },
        "title": _non_empty_string(),
        "description": {"type": "string", "default": ""},
        "schema_version": {"type": "integer", "minimum": 1, "default": 1},
        "harness_id": _non_empty_string(),
        "instructions": {
            **_non_empty_string(),
            "description": "Role instructions. Secret-looking literal values are rejected by the runtime parser.",
        },
        "model": _nullable_string(),
        "reasoning_effort": {
            "type": ["string", "null"],
            "enum": [None, "none", "low", "medium", "high"],
            "default": None,
        },
        "api_mode": {
            "type": "string",
            "enum": _enum_values(GigaChatApiMode),
            "default": GigaChatApiMode.V2.value,
        },
        "invocation_mode": {
            "type": "string",
            "enum": ["headless", "native"],
            "default": "headless",
        },
        "mode": {
            "type": "string",
            "enum": sorted(ALLOWED_MODES),
            "default": "plan",
        },
        "workspace_policy": {
            "type": "string",
            "enum": sorted(ALLOWED_WORKSPACE_POLICIES),
            "default": "auto",
        },
        "permission_profile": {
            "type": "string",
            "enum": ["interactive", "review_every_action", "unattended"],
            "default": "interactive",
        },
        "prompt_files": path_array,
        "skills": non_empty_array,
        "memory_selectors": non_empty_array,
        "context_selectors": path_array,
        "tool_ids": non_empty_array,
        "allowed_tools": tool_array,
        "disallowed_tools": tool_array,
        "budgets": {
            "type": "object",
            "description": "Execution limits; secret-bearing extension keys or values remain forbidden.",
            "properties": {
                "timeout_seconds": _nullable_positive_integer(),
                "max_tokens": _nullable_positive_integer(),
                "max_attempts": {"type": "integer", "minimum": 1, "default": 1},
                "max_concurrency": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                },
            },
            "additionalProperties": True,
            "default": {"max_attempts": 1, "max_concurrency": 1},
        },
        "expected_artifact": _nullable_string(),
        "provenance": {
            "type": "object",
            "description": "Redaction-safe provenance only; secret literals and secret-looking keys are rejected.",
            "additionalProperties": True,
            "default": {},
        },
    }
    return _root_schema(
        filename,
        title="GigaLoom Agent Profile v1",
        description="Strict editor contract for .giga/agents YAML parsed by parse_agent_profile().",
        properties=properties,
        required=("id", "title", "harness_id", "instructions"),
        additional_properties=False,
    )


def _workflow_schema(filename: str) -> dict[str, Any]:
    step_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "id": _workflow_id(),
            "kind": {
                "type": "string",
                "enum": _enum_values(WorkflowStepKind),
            },
            "title": {"type": "string"},
            "depends_on": _string_array(),
            "condition": {
                "type": "string",
                "enum": ["on_success", "on_failure", "always"],
                "default": "on_success",
            },
            "agent_id": _non_empty_string(),
            "prompt": {"type": "string"},
            "eval_id": _non_empty_string(),
            "harness_ids": _string_array(min_items=1),
            "action": {"type": "string"},
            "transform": {"type": "string"},
            "select": _string_array(),
            "artifact_types": {
                **_string_array(),
                "items": {"type": "string", "enum": sorted(HANDOFF_ARTIFACT_TYPES)},
            },
            "retries": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "default": 0,
            },
            "timeout_seconds": _nullable_bounded_integer(1, 86400),
            "max_fan_out": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_FAN_OUT,
                "default": 1,
            },
            "inputs": {
                "type": "object",
                "description": "Arbitrary redaction-safe step inputs; secret values are redacted before storage.",
                "additionalProperties": True,
                "default": {},
            },
            "output": {"type": "string"},
        },
        "required": ["id", "kind"],
        # Project workflow loading intentionally preserves extension fields.
        "additionalProperties": True,
        "allOf": [
            _conditional_required("agent", "agent_id"),
            _conditional_required("arena", "harness_ids"),
            _conditional_required("eval", "eval_id"),
            {
                "if": {"properties": {"kind": {"const": "approval"}}},
                "then": {
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": _enum_values(PermissionAction),
                            "default": PermissionAction.EXTERNAL_WRITE.value,
                        }
                    }
                },
            },
            {
                "if": {"properties": {"kind": {"const": "transform"}}},
                "then": {
                    "properties": {
                        "transform": {
                            "type": "string",
                            "enum": ["identity", "select", "template"],
                        }
                    },
                    "required": ["transform"],
                },
            },
        ],
    }
    properties = {
        "id": _workflow_id(),
        "title": _non_empty_string(),
        "description": {"type": "string", "default": ""},
        "schema_version": {"type": "integer", "const": 1, "default": 1},
        "version": _non_empty_string(),
        "inputs": {
            "type": "object",
            "additionalProperties": True,
            "default": {},
        },
        "provenance": {
            "type": "object",
            "description": "Redaction-safe workflow provenance; secret values are redacted before storage.",
            "additionalProperties": True,
            "default": {},
        },
        "budgets": {
            "type": "object",
            "properties": {
                "max_concurrency": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_FAN_OUT,
                    "default": 1,
                },
                "max_steps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_WORKFLOW_STEPS,
                    "default": MAX_WORKFLOW_STEPS,
                },
                "timeout_seconds": _nullable_bounded_integer(1, 86400),
            },
            "additionalProperties": True,
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_WORKFLOW_STEPS,
            "items": step_schema,
        },
    }
    return _root_schema(
        filename,
        title="GigaLoom Workflow v1",
        description=(
            "Editor contract for .giga/workflows YAML. Project loading admits "
            "compatibility extension fields, so additionalProperties remains true."
        ),
        properties=properties,
        required=("id", "title", "version", "steps"),
        additional_properties=True,
    )


def _eval_schema(filename: str) -> dict[str, Any]:
    check_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string", "enum": sorted(CHECK_TYPES)},
            "value": {},
            "case_sensitive": {"type": "boolean", "default": True},
        },
        "required": ["type", "value"],
        "additionalProperties": True,
    }
    case_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "prompt": _non_empty_string(),
            "harnesses": _string_array(),
            "checks": {"type": "array", "items": check_schema, "default": []},
            "required_capability": {
                "type": "string",
                "enum": _enum_values(HarnessCapability),
            },
        },
        "required": ["prompt"],
        "additionalProperties": True,
    }
    properties = {
        "name": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9_.-]+$",
            "description": "Defaults to the YAML filename stem when omitted.",
        },
        "description": {"type": "string"},
        "harnesses": _string_array(),
        "model": {"type": "string"},
        "api_mode": {
            "type": "string",
            "enum": _enum_values(GigaChatApiMode),
            "default": GigaChatApiMode.V2.value,
        },
        "mode": {"type": "string", "default": "plan"},
        "workspace_policy": {"type": "string", "default": "current"},
        "cases": {"type": "array", "minItems": 1, "items": case_schema},
        "metadata": {
            "type": "object",
            "description": "Redaction-safe metadata only; secret values are redacted before serialization.",
            "additionalProperties": True,
            "default": {},
        },
    }
    return _root_schema(
        filename,
        title="GigaLoom Evaluation Spec v1",
        description=(
            "Editor contract for .giga/evals YAML parsed by eval_spec_from_mapping(). "
            "Unknown extension fields remain compatible with the runtime."
        ),
        properties=properties,
        required=("cases",),
        additional_properties=True,
    )


def _schedule_source_schema(filename: str) -> dict[str, Any]:
    target = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["agent", "preset", "workflow", "eval"],
            },
            "id": _safe_schedule_id(),
        },
        "required": ["kind", "id"],
        "additionalProperties": True,
    }
    cadence = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["once", "interval", "rrule"]},
            "timezone": {
                **_non_empty_string(),
                "description": "Explicit IANA timezone validated by zoneinfo at runtime.",
            },
            "start_at": {
                "type": "string",
                "description": "ISO-8601 local datetime; defaults to the current local time.",
            },
            "interval_seconds": {"type": "number", "exclusiveMinimum": 0},
            "rrule": {"type": "string", "minLength": 1},
        },
        "required": ["kind", "timezone"],
        "additionalProperties": True,
        "allOf": [
            _conditional_required("interval", "interval_seconds"),
            _conditional_required("rrule", "rrule"),
        ],
    }
    positive_number = {"type": "number", "exclusiveMinimum": 0}
    positive_integer = {"type": "integer", "minimum": 1}
    properties = {
        "id": _safe_schedule_id(),
        "title": {"type": "string"},
        "target": target,
        "cadence": cadence,
        "prompt": {"type": "string"},
        "inputs": {"type": "object", "additionalProperties": True},
        "destination": {
            "type": "string",
            "enum": ["new_task", "resume"],
            "default": "new_task",
        },
        "session_id": {"type": "string", "minLength": 1},
        "workspace_policy": {
            "type": "string",
            "const": "worktree",
            "default": "worktree",
        },
        "timeout_seconds": {**positive_number, "default": 3600.0},
        "max_attempts": {**positive_integer, "default": 1},
        "overlap_policy": {
            "type": "string",
            "enum": ["skip", "allow"],
            "default": "skip",
        },
        "max_concurrency": {**positive_integer, "default": 1},
        "misfire_policy": {
            "type": "string",
            "enum": ["skip", "run_once"],
            "default": "skip",
        },
        "misfire_grace_seconds": {**positive_number, "default": 60.0},
        "notifications": {
            "type": "object",
            "properties": {"desktop": {"type": "boolean", "default": False}},
            "additionalProperties": True,
            "default": {"desktop": False},
        },
    }
    schema = _root_schema(
        filename,
        title="GigaLoom Schedule Source v1",
        description=(
            "Editor contract for shareable .giga/schedule-sources YAML consumed by "
            "build_schedule_definition(). Generated .giga/schedules snapshots are not source files."
        ),
        properties=properties,
        required=("id", "target", "cadence"),
        additional_properties=True,
    )
    schema["allOf"] = [
        {
            "if": {"properties": {"destination": {"const": "resume"}}},
            "then": {"required": ["session_id"]},
        }
    ]
    return schema


def _non_empty_string() -> dict[str, Any]:
    return {"type": "string", "minLength": 1}


def _nullable_string() -> dict[str, Any]:
    return {"type": ["string", "null"], "default": None}


def _nullable_positive_integer() -> dict[str, Any]:
    return {"type": ["integer", "null"], "minimum": 1, "default": None}


def _nullable_bounded_integer(minimum: int, maximum: int) -> dict[str, Any]:
    return {
        "type": ["integer", "null"],
        "minimum": minimum,
        "maximum": maximum,
        "default": None,
    }


def _string_array(*, min_items: int = 0) -> dict[str, Any]:
    return {
        "type": "array",
        "items": _non_empty_string(),
        "minItems": min_items,
        "default": [],
    }


def _workflow_id() -> dict[str, Any]:
    return {"type": "string", "pattern": WORKFLOW_ID_PATTERN.pattern}


def _safe_schedule_id() -> dict[str, Any]:
    return {"type": "string", "pattern": r"^[A-Za-z0-9_-]+$"}


def _conditional_required(kind: str, field: str) -> dict[str, Any]:
    return {
        "if": {"properties": {"kind": {"const": kind}}, "required": ["kind"]},
        "then": {"required": [field]},
    }


def _enum_values(enum_type: type[Any]) -> list[str]:
    return [str(item.value) for item in enum_type]
