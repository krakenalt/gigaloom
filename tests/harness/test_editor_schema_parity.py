from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
import pytest
import yaml

from gigaloom.automation.agents.api import (
    AGENT_SCHEMA_VERSION,
    parse_agent_profile,
)
from gigaloom.automation.evaluations.api import eval_spec_from_mapping
from gigaloom.automation.schemas.api import artifact_schema, render_artifact_schema
from gigaloom.automation.schedules.api import build_schedule_definition
from gigaloom.automation.workflows.api import parse_workflow_definition
from gigaloom.projects.api import resolve_project


FIXTURES = Path(__file__).parents[1] / "fixtures" / "editor_schemas"


def test_v1_fixtures_validate_and_parse_through_authoritative_entrypoints(
    tmp_path: Path,
) -> None:
    agent_data, agent_text = _fixture("v1/agent.yaml")
    workflow_data, workflow_text = _fixture("v1/workflow.yaml")
    eval_data, _ = _fixture("v1/eval.yaml")
    schedule_data, _ = _fixture("v1/schedule-source.yaml")

    _assert_schema_valid("agent", agent_data)
    _assert_schema_valid("workflow", workflow_data)
    _assert_schema_valid("eval", eval_data)
    _assert_schema_valid("schedule-source", schedule_data)

    assert parse_agent_profile(agent_text).id == "schema-fixture"
    assert parse_workflow_definition(workflow_text, allow_unknown=True).id == (
        "schema-fixture"
    )
    assert eval_spec_from_mapping(eval_data, path=Path("schema-fixture.yaml")).name == (
        "schema-fixture"
    )
    project = _project_with_agent(tmp_path, agent_text)
    assert build_schedule_definition(project, schedule_data).id == "schema-fixture"


def test_required_fields_fail_in_schema_and_parser(tmp_path: Path) -> None:
    agent_data, _ = _fixture("v1/agent.yaml")
    workflow_data, _ = _fixture("v1/workflow.yaml")
    eval_data, _ = _fixture("v1/eval.yaml")
    schedule_data, _ = _fixture("v1/schedule-source.yaml")

    for field in ("id", "title", "harness_id", "instructions"):
        invalid = {key: value for key, value in agent_data.items() if key != field}
        _assert_schema_invalid("agent", invalid)
        with pytest.raises(ValueError):
            parse_agent_profile(yaml.safe_dump(invalid))

    for field in ("id", "title", "version", "steps"):
        invalid = {key: value for key, value in workflow_data.items() if key != field}
        _assert_schema_invalid("workflow", invalid)
        with pytest.raises(ValueError):
            parse_workflow_definition(yaml.safe_dump(invalid), allow_unknown=True)

    invalid_eval = {key: value for key, value in eval_data.items() if key != "cases"}
    _assert_schema_invalid("eval", invalid_eval)
    with pytest.raises(ValueError, match="at least one case"):
        eval_spec_from_mapping(invalid_eval, path=Path("invalid.yaml"))

    project = _project_with_agent(tmp_path, yaml.safe_dump(agent_data))
    for field in ("id", "target", "cadence"):
        invalid = {key: value for key, value in schedule_data.items() if key != field}
        _assert_schema_invalid("schedule-source", invalid)
        with pytest.raises(ValueError):
            build_schedule_definition(project, invalid)


def test_defaults_and_enums_match_parser_results(tmp_path: Path) -> None:
    agent_data, _ = _fixture("v1/agent.yaml")
    minimal_agent = {
        key: agent_data[key] for key in ("id", "title", "harness_id", "instructions")
    }
    parsed_agent = parse_agent_profile(yaml.safe_dump(minimal_agent))
    agent_properties = artifact_schema("agent").document["properties"]
    assert parsed_agent.schema_version == agent_properties["schema_version"]["default"]
    assert parsed_agent.api_mode == agent_properties["api_mode"]["default"]
    assert (
        parsed_agent.invocation_mode == agent_properties["invocation_mode"]["default"]
    )
    assert parsed_agent.mode == agent_properties["mode"]["default"]
    assert (
        parsed_agent.workspace_policy == agent_properties["workspace_policy"]["default"]
    )
    assert (
        parsed_agent.permission_profile
        == agent_properties["permission_profile"]["default"]
    )
    assert [] == agent_properties["skills"]["default"]
    _assert_schema_valid("agent", {**minimal_agent, "skills": []})

    workflow_data, _ = _fixture("v1/workflow.yaml")
    workflow_data.pop("schema_version")
    workflow_data.pop("budgets")
    parsed_workflow = parse_workflow_definition(
        yaml.safe_dump(workflow_data), allow_unknown=True
    )
    workflow_properties = artifact_schema("workflow").document["properties"]
    assert (
        parsed_workflow.schema_version
        == workflow_properties["schema_version"]["default"]
    )
    assert (
        parsed_workflow.budgets.max_concurrency
        == workflow_properties["budgets"]["properties"]["max_concurrency"]["default"]
    )

    eval_data, _ = _fixture("v1/eval.yaml")
    for field in ("api_mode", "mode", "workspace_policy"):
        eval_data.pop(field)
    parsed_eval = eval_spec_from_mapping(eval_data, path=Path("schema-fixture.yaml"))
    eval_properties = artifact_schema("eval").document["properties"]
    assert parsed_eval.api_mode.value == eval_properties["api_mode"]["default"]
    assert parsed_eval.mode == eval_properties["mode"]["default"]
    assert (
        parsed_eval.workspace_policy == eval_properties["workspace_policy"]["default"]
    )

    agent_data, agent_text = _fixture("v1/agent.yaml")
    schedule_data, _ = _fixture("v1/schedule-source.yaml")
    for field in (
        "workspace_policy",
        "destination",
        "timeout_seconds",
        "max_attempts",
        "overlap_policy",
        "max_concurrency",
        "misfire_policy",
        "misfire_grace_seconds",
        "notifications",
    ):
        schedule_data.pop(field)
    parsed_schedule = build_schedule_definition(
        _project_with_agent(tmp_path, agent_text), schedule_data
    )
    schedule_properties = artifact_schema("schedule-source").document["properties"]
    assert (
        parsed_schedule.workspace_policy
        == schedule_properties["workspace_policy"]["default"]
    )
    assert parsed_schedule.destination == schedule_properties["destination"]["default"]
    assert (
        parsed_schedule.overlap_policy
        == schedule_properties["overlap_policy"]["default"]
    )


def test_unknown_field_behavior_matches_each_project_loader(tmp_path: Path) -> None:
    agent_data, _ = _fixture("v1/agent.yaml")
    agent_data["x_extension"] = True
    _assert_schema_invalid("agent", agent_data)
    with pytest.raises(ValueError, match="Unknown agent profile fields"):
        parse_agent_profile(yaml.safe_dump(agent_data))

    workflow_data, _ = _fixture("v1/workflow.yaml")
    workflow_data["x_extension"] = True
    workflow_data["steps"][0]["x_step_extension"] = "kept-compatible"
    _assert_schema_valid("workflow", workflow_data)
    parsed_workflow = parse_workflow_definition(
        yaml.safe_dump(workflow_data), allow_unknown=True
    )
    assert parsed_workflow.id == "schema-fixture"
    with pytest.raises(ValueError, match="Unknown workflow fields"):
        parse_workflow_definition(yaml.safe_dump(workflow_data))

    eval_data, _ = _fixture("v1/eval.yaml")
    eval_data["x_extension"] = True
    eval_data["cases"][0]["x_case_extension"] = True
    _assert_schema_valid("eval", eval_data)
    assert eval_spec_from_mapping(eval_data, path=Path("fixture.yaml")).cases

    agent_data.pop("x_extension")
    schedule_data, _ = _fixture("v1/schedule-source.yaml")
    schedule_data["x_extension"] = True
    schedule_data["cadence"]["x_cadence_extension"] = True
    _assert_schema_valid("schedule-source", schedule_data)
    project = _project_with_agent(tmp_path, yaml.safe_dump(agent_data))
    assert build_schedule_definition(project, schedule_data).id == "schema-fixture"


def test_future_schema_versions_fail_closed_until_migration_exists() -> None:
    agent_data, agent_text = _fixture("future/agent-v2.yaml")
    workflow_data, workflow_text = _fixture("future/workflow-v2.yaml")

    assert AGENT_SCHEMA_VERSION == 1
    _assert_schema_invalid("agent", agent_data)
    with pytest.raises(ValueError, match="Unsupported agent schema_version: 2"):
        parse_agent_profile(agent_text)

    _assert_schema_invalid("workflow", workflow_data)
    with pytest.raises(ValueError, match="schema_version must be between 1 and 1"):
        parse_workflow_definition(workflow_text, allow_unknown=True)


def test_schema_digest_snapshot_fails_on_unreviewed_drift() -> None:
    expected = json.loads((FIXTURES / "schema-digests.json").read_text())
    actual = {
        artifact_schema(name).filename: hashlib.sha256(
            render_artifact_schema(name).encode()
        ).hexdigest()
        for name in ("agent", "workflow", "eval", "schedule-source")
    }

    assert actual == expected


def _fixture(relative: str) -> tuple[dict[str, Any], str]:
    text = (FIXTURES / relative).read_text(encoding="utf-8")
    value = yaml.safe_load(text)
    assert isinstance(value, dict)
    return value, text


def _assert_schema_valid(name: str, value: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(artifact_schema(name).document)
    assert list(validator.iter_errors(value)) == []


def _assert_schema_invalid(name: str, value: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(artifact_schema(name).document)
    assert list(validator.iter_errors(value))


def _project_with_agent(tmp_path: Path, agent_text: str):
    root = tmp_path / "project"
    agent_directory = root / ".giga" / "agents"
    agent_directory.mkdir(parents=True, exist_ok=True)
    (agent_directory / "schema-fixture.yaml").write_text(agent_text, encoding="utf-8")
    return resolve_project(root, data_dir=tmp_path / "data", load_config_name=False)
