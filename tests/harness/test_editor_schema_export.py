from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import yaml

from gigaloom.automation.agents.api import parse_agent_profile, render_starter_agent
from gigaloom.automation.schemas.api import (
    add_yaml_language_server_header,
    all_artifact_schemas,
    export_artifact_schema,
    packaged_schema_text,
    render_artifact_schema,
    yaml_language_server_header,
)
from gigaloom.automation.schemas.cli import (
    _handle_schema_export,
    _handle_schema_list,
    register_schema_commands,
)
from gigaloom.automation.workflows.api import (
    parse_workflow_definition,
    render_review_team_workflow,
)


def test_packaged_schema_resources_match_generator_and_export(tmp_path: Path) -> None:
    for descriptor in all_artifact_schemas():
        packaged = packaged_schema_text(descriptor.name)
        assert packaged == render_artifact_schema(descriptor.name)
        Draft202012Validator.check_schema(json.loads(packaged))
        output = tmp_path / "nested" / descriptor.filename
        assert export_artifact_schema(descriptor.name, output) == output
        assert output.read_text(encoding="utf-8") == packaged


def test_schema_cli_handlers_list_print_and_export(
    tmp_path: Path,
    capsys,
) -> None:
    parser = argparse.ArgumentParser(prog="giga")
    register_schema_commands(parser.add_subparsers(dest="command"))

    listed = parser.parse_args(["schema", "list", "--json"])
    assert _handle_schema_list(listed) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in payload["schemas"]] == [
        "agent",
        "workflow",
        "eval",
        "schedule-source",
    ]
    assert all(len(item["sha256"]) == 64 for item in payload["schemas"])

    output = tmp_path / "workflow.schema.json"
    exported = parser.parse_args(["schema", "workflow", "--output", str(output)])
    assert _handle_schema_export(exported) == 0
    assert capsys.readouterr().out == ""
    assert output.read_text(encoding="utf-8") == packaged_schema_text("workflow")

    printed = parser.parse_args(["schema", "eval"])
    assert _handle_schema_export(printed) == 0
    assert capsys.readouterr().out == packaged_schema_text("eval")


def test_generated_starters_include_parseable_local_yls_headers() -> None:
    agent = render_starter_agent("planner")
    workflow = render_review_team_workflow()

    assert agent.startswith(yaml_language_server_header("agent"))
    assert workflow.startswith(yaml_language_server_header("workflow"))
    assert parse_agent_profile(agent).id == "planner"
    assert parse_workflow_definition(workflow, allow_unknown=True).id == "review-team"
    assert isinstance(yaml.safe_load(agent), dict)
    assert add_yaml_language_server_header(agent, "agent") == agent
