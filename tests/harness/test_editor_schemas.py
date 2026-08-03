import hashlib

from jsonschema import Draft202012Validator

from gigaloom.automation.schemas.api import (
    ArtifactSchemaName,
    all_artifact_schemas,
    artifact_schema,
    render_artifact_schema,
)


def test_editor_schema_catalog_is_versioned_and_deterministic() -> None:
    schemas = all_artifact_schemas()

    assert [schema.name.value for schema in schemas] == [
        "agent",
        "workflow",
        "eval",
        "schedule-source",
    ]
    assert [schema.filename for schema in schemas] == [
        "agent.schema.json",
        "workflow.schema.json",
        "eval.schema.json",
        "schedule-source.schema.json",
    ]
    for schema in schemas:
        Draft202012Validator.check_schema(schema.document)
        assert schema.schema_version == 1
        assert schema.document["x-gigaloom-schema-version"] == 1
        rendered = render_artifact_schema(schema.name)
        assert rendered.endswith("\n")
        assert rendered == render_artifact_schema(schema.name.value)
        assert hashlib.sha256(rendered.encode()).hexdigest()


def test_schema_strictness_matches_project_parser_entrypoints() -> None:
    agent = artifact_schema(ArtifactSchemaName.AGENT).document
    workflow = artifact_schema(ArtifactSchemaName.WORKFLOW).document
    eval_spec = artifact_schema(ArtifactSchemaName.EVAL).document
    schedule = artifact_schema(ArtifactSchemaName.SCHEDULE_SOURCE).document

    assert agent["additionalProperties"] is False
    assert workflow["additionalProperties"] is True
    assert workflow["properties"]["steps"]["items"]["additionalProperties"] is True
    assert eval_spec["additionalProperties"] is True
    assert schedule["additionalProperties"] is True


def test_secret_restrictions_are_visible_without_claiming_schema_enforcement() -> None:
    agent = artifact_schema("agent").document

    assert (
        "Secret-looking literal values are rejected"
        in agent["properties"]["instructions"]["description"]
    )
    assert "secret literals" in agent["properties"]["provenance"]["description"]
